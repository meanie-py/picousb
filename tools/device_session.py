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
    async with DevBox(args.url, token=token) as box:
        await box.status()
        print("Authenticated device status received.", flush=True)
        if args.serve is not None:
            await box.storage.serve_directory(args.serve)
            print("Directory backend attached; safely eject before stopping.", flush=True)
            while not box.storage.ejected.is_set():
                await asyncio.sleep(1)
                await box.status()
            print("Target ejected media; directory synchronized.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Credential-safe local device session")
    parser.add_argument("--url", default="ws://picousb.local:8080/ws")
    parser.add_argument("--config", type=Path, default=Path(".env.flash"))
    parser.add_argument("--serve", type=Path)
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
