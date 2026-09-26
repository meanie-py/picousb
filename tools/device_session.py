"""Use local credentials opaquely for status or a hardware storage session.

Never log credentials, configuration, network payloads, or exception details.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from config_block import parse_env
from pico_devbox import DevBox


async def run(args: argparse.Namespace) -> None:
    values = parse_env(args.config.read_text(encoding="utf-8-sig"))
    token = values.pop("DEVICE_TOKEN", "")
    values.clear()
    if getattr(args, "wait_ready", 0):
        import json

        box = DevBox(args.url, token=token)
        try:
            print(
                json.dumps(
                    await box.wait_ready(timeout=args.wait_ready, usb=not args.network_only)
                ),
                flush=True,
            )
        finally:
            await box.close()
        return
    async with DevBox(args.url, token=token, udp_reads=getattr(args, "udp_reads", False)) as box:
        await box.status()
        print("Authenticated device status received.", flush=True)
        image = getattr(args, "image", None)
        if args.serve is not None or image is not None:
            if image is not None:
                await box.storage.serve_image(image, read_only=not args.writable)
            else:
                await box.storage.serve_directory(args.serve)
            print("Backend attached; safely eject before stopping.", flush=True)
            ticks = 0
            while not box.storage.ejected.is_set():
                await asyncio.sleep(1)
                await box.status()
                ticks += 1
                if ticks % 10 == 0:
                    if box._udp:
                        print(
                            f"UDP: batches={box._udp.batches} packets={box._udp.packets} retries={box._udp.retries}",
                            flush=True,
                        )
                    print(
                        f"Storage: read={box.storage.read_bytes} written={box.storage.written_bytes} requests={box.storage.requests} errors={box.storage.errors}",
                        flush=True,
                    )
            print("Target ejected media; backend synchronized.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Credential-safe local device session")
    parser.add_argument("--url", default="ws://picousb.local:8080/ws")
    parser.add_argument("--config", type=Path, default=Path(".env.flash"))
    media = parser.add_mutually_exclusive_group()
    media.add_argument("--serve", type=Path)
    media.add_argument("--image", type=Path)
    parser.add_argument("--writable", action="store_true")
    parser.add_argument("--udp-reads", action="store_true")
    parser.add_argument("--wait-ready", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--network-only", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Session stopped; unexported sectors remain in the recovery image.")
    except Exception:
        print(
            "Device session failed. Check connectivity/configuration locally; details suppressed."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
