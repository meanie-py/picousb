import argparse
import asyncio
import json
import os

from .auth import cli_token
from .client import DevBox
from .protocol import DevBoxError


async def run(args: argparse.Namespace) -> None:
    token = cli_token()
    box = DevBox(
        args.url,
        token,
        udp_reads=getattr(args, "udp_reads", False),
        auto_reconnect=getattr(args, "auto_reconnect", False),
    )
    if args.command == "wait-ready":
        try:
            print(json.dumps(await box.wait_ready(timeout=args.timeout, usb=not args.network_only)))
        finally:
            await box.close()
        return
    async with box:
        if args.command in ("status", "connect"):
            print(json.dumps(await box.status()))
        elif args.command == "type":
            await box.keyboard.type(args.text, delay_ms=getattr(args, "delay", 0))
            await box.drain()
        elif args.command == "key":
            await box.keyboard.press(
                args.keys[-1],
                *args.keys[:-1],
                hold_ms=getattr(args, "hold", 0),
                repeat=getattr(args, "repeat", 1),
                interval_ms=getattr(args, "interval", 0),
            )
            await box.drain()
        elif args.command == "raw-report":
            await box.raw_report(
                {"keyboard": 0, "mouse": 1, "pointer": 2}[args.interface], bytes.fromhex(args.hex)
            )
            await box.drain()
        elif args.command == "pointer":
            if args.action == "click":
                await box.pointer.click(args.x, args.y, args.button)
            else:
                await box.pointer.move(args.x, args.y)
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
    p.add_argument("--udp-reads", action="store_true", help="Experimental reliable UDP read data")
    p.add_argument(
        "--auto-reconnect",
        action="store_true",
        help="Reconnect before new operations; never replay uncertain input",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("connect")
    ready = sub.add_parser("wait-ready")
    ready.add_argument("--timeout", type=float, default=30)
    ready.add_argument("--network-only", action="store_true")
    typing = sub.add_parser("type")
    typing.add_argument("text")
    typing.add_argument("--delay", type=int, default=0, metavar="MS")
    key = sub.add_parser("key")
    key.add_argument("keys", nargs="+")
    key.add_argument("--hold", type=int, default=0, metavar="MS")
    key.add_argument("--repeat", type=int, default=1, metavar="N")
    key.add_argument("--interval", type=int, default=0, metavar="MS")
    raw = sub.add_parser("raw-report")
    raw.add_argument("interface", choices=["keyboard", "mouse", "pointer"])
    raw.add_argument("hex")
    pointer = sub.add_parser("pointer").add_subparsers(dest="action", required=True)
    for action in ("move", "click"):
        command = pointer.add_parser(action)
        command.add_argument("x", type=int)
        command.add_argument("y", type=int)
        if action == "click":
            command.add_argument("--button", choices=["left", "right", "middle"], default="left")
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
    except Exception as error:
        code, category = 1, "other"
        if isinstance(error, (OSError, TimeoutError)):
            code, category = 3, "unreachable"
        if isinstance(error, DevBoxError):
            code, category = {
                2: (4, "auth_failed"),
                5: (3, "unreachable"),
                9: (5, "usb_not_mounted"),
                10: (6, "host_not_polling"),
                11: (7, "usb_reset"),
            }.get(error.code, (1, "other"))
        p.exit(code, json.dumps({"ok": False, "error": category, "exit_code": code}) + "\n")


if __name__ == "__main__":
    main()
