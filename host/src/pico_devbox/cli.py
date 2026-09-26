import argparse
import asyncio
import getpass
import json
import os

from .client import DevBox


async def run(args: argparse.Namespace) -> None:
    token = os.environ.get("PICO_DEVICE_TOKEN")
    if token is None:
        token = getpass.getpass("Device token (blank if disabled): ")
    async with DevBox(args.url, token) as box:
        if args.command in ("status", "connect"):
            print(json.dumps(await box.status()))
        elif args.command == "type":
            await box.keyboard.type(args.text)
            await box.drain()
        elif args.command == "key":
            await box.keyboard.press(args.keys[-1], *args.keys[:-1])
            await box.drain()
        elif args.command == "mouse":
            if args.action == "move":
                await box.mouse.move(args.x, args.y)
            elif args.action == "click":
                await box.mouse.click(args.button)
            else:
                await box.mouse.scroll(args.amount)
            await box.drain()
        elif args.command in ("serve", "serve-image"):
            if args.command == "serve-image":
                await box.storage.serve_image(args.image, read_only=not args.writable)
            else:
                await box.storage.serve_directory(args.directory)
            print("Serving media. Safely eject on target before stopping.")
            while box.ws and box.ws.close_code is None and not box.storage.ejected.is_set():
                await asyncio.sleep(0.1)
            if not box.storage.ejected.is_set():
                raise RuntimeError("connection lost; recovery image retained")
            print("Target ejected; media synchronized.")


def main() -> None:
    p = argparse.ArgumentParser(prog="pico-devbox")
    p.add_argument("--url", default=os.environ.get("PICO_URL", "ws://picousb.local:8080/ws"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("connect")
    sub.add_parser("type").add_argument("text")
    sub.add_parser("key").add_argument("keys", nargs="+")
    sub.add_parser("serve").add_argument("directory")
    image = sub.add_parser("serve-image")
    image.add_argument("image")
    image.add_argument("--writable", action="store_true")
    mouse = sub.add_parser("mouse").add_subparsers(dest="action", required=True)
    move = mouse.add_parser("move")
    move.add_argument("x", type=int)
    move.add_argument("y", type=int)
    mouse.add_parser("click").add_argument("button", choices=["left", "right", "middle"])
    mouse.add_parser("scroll").add_argument("amount", type=int)
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        p.exit(1, "Operation failed. Check connection, authentication, and volume state locally.\n")


if __name__ == "__main__":
    main()
