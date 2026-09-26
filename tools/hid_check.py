"""Interactive, focus-guarded real USB HID acceptance; secrets stay local."""

import argparse
import asyncio
import ctypes
import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from config_block import parse_env
from pico_devbox import DevBox

parser = argparse.ArgumentParser(description="Interactive Windows Pico USB HID check")
parser.add_argument("--url", default="ws://picousb.local:8080/ws")
parser.add_argument("--config", type=Path, default=Path(".env.flash"))
args = parser.parse_args()
logging.disable(logging.CRITICAL)
root = tk.Tk()
root.title("PicoUSB hardware input test")
root.geometry("760x620")
tk.Label(root, text="PicoUSB hardware input test", font=("Segoe UI", 20)).pack(pady=14)
tk.Label(
    root,
    text="Click Run test, then leave this window focused. Do not type or move the mouse.\n"
    "The Pico will type test text and exercise its mouse. No GUI/Super shortcut is sent.",
).pack()
entry = tk.Entry(root, font=("Consolas", 18), width=42)
entry.pack(pady=20)
status = tk.StringVar(value="Ready. The test takes about five seconds.")
tk.Label(root, textvariable=status, wraplength=700, font=("Segoe UI", 12)).pack(pady=12)
events = []
observed = {}
active = False
results = queue.Queue()
u = ctypes.WinDLL("user32")
u.GetForegroundWindow.restype = ctypes.c_void_p
u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u.GetAncestor.restype = ctypes.c_void_p
u.GetAsyncKeyState.argtypes = [ctypes.c_int]
u.GetAsyncKeyState.restype = ctypes.c_short


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def position():
    point = Point()
    u.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


root.update()
window = u.GetAncestor(root.winfo_id(), 2)


def capture(event, kind):
    if active:
        events.append((kind, event.keysym, event.num, event.x_root, event.y_root))


for sequence, kind in (
    ("<KeyPress>", "down"),
    ("<KeyRelease>", "up"),
    ("<Motion>", "move"),
    ("<ButtonPress>", "button_down"),
    ("<ButtonRelease>", "button_up"),
    ("<MouseWheel>", "wheel"),
):
    root.bind_all(sequence, lambda e, kind=kind: capture(e, kind), add=True)


async def exercise():
    values = parse_env(args.config.read_text(encoding="utf-8-sig"))
    token = values.pop("DEVICE_TOKEN", "")
    values.clear()
    box = DevBox(args.url, token=token)

    async def act(operation):
        if u.GetForegroundWindow() != window:
            raise RuntimeError("focus lost")
        await operation()
        await box.drain()
        await asyncio.sleep(0.15)

    try:
        await box.connect()
        await asyncio.sleep(0.5)
        await act(lambda: box.keyboard.type("PicoUSB Aa1!", delay_ms=5))
        await act(lambda: box.keyboard.press("ENTER"))
        for key, vk in (("CTRL", 0x11), ("ALT", 0x12)):
            await act(lambda key=key: box.keyboard.key_down(key))
            observed[key] = bool(u.GetAsyncKeyState(vk) & 0x8000)
            await act(lambda key=key: box.keyboard.key_up(key))
            observed[key] &= not bool(u.GetAsyncKeyState(vk) & 0x8000)
        before = position()
        await act(lambda: box.mouse.move(25, 10))
        observed["motion"] = position() != before
        for button in ("left", "right", "middle"):
            await act(lambda button=button: box.mouse.click(button))
        await act(lambda: box.mouse.scroll(1))
        x, y = observed["pointer_target"]
        width, height = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        if not (0 <= x < width and 0 <= y < height):
            raise RuntimeError("move test window onto primary display")
        await act(
            lambda: box.pointer.move(
                round(x * 32767 / (width - 1)), round(y * 32767 / (height - 1))
            )
        )
        actual = position()
        observed["absolute"] = abs(actual[0] - x) <= 5 and abs(actual[1] - y) <= 5
        if not observed["absolute"]:
            raise RuntimeError("absolute mapping differs; do not click outside the test target")
        await act(
            lambda: box.pointer.click(
                round(x * 32767 / (width - 1)), round(y * 32767 / (height - 1))
            )
        )
        await act(lambda: box.keyboard.press("F1", hold_ms=40, repeat=3, interval_ms=40))
        await act(lambda: box.raw_report(0, bytes(8)))
        telemetry = await box.usb_status()
        observed["completions"] = all(
            h.completed > 0 and h.accepted == h.completed for h in telemetry.hid
        )
        await act(lambda: box.keyboard.key_down("SHIFT"))
        observed["shift_down"] = bool(u.GetAsyncKeyState(0x10) & 0x8000)
    finally:
        await box.close()  # firmware must release held Shift on disconnect
    await asyncio.sleep(0.5)
    observed["shift_up"] = not bool(u.GetAsyncKeyState(0x10) & 0x8000)


def worker():
    try:
        asyncio.run(exercise())
        results.put(True)
    except Exception:
        results.put(False)  # never print configuration or exception details


def start():
    global active
    entry.delete(0, tk.END)
    entry.focus_set()
    events.clear()
    observed.clear()
    observed["pointer_target"] = (
        entry.winfo_rootx() + entry.winfo_width() // 2,
        entry.winfo_rooty() + entry.winfo_height() // 2,
    )
    active = True
    button.configure(state=tk.DISABLED)
    status.set("Testing real USB input. Keep this window focused...")
    threading.Thread(target=worker, daemon=True).start()


button = tk.Button(root, text="Run test", command=start, font=("Segoe UI", 16), width=22, height=3)
button.pack(pady=18)


def poll():
    global active
    try:
        ok = results.get_nowait()
    except queue.Empty:
        root.after(100, poll)
        return
    active = False
    kinds = {e[0] for e in events}
    keys = {e[1] for e in events if e[0] == "down"}
    checks = {
        "connection and focus": ok,
        "ASCII typing": entry.get() == "PicoUSB Aa1!",
        "Enter, Ctrl, Alt, Shift": "Return" in keys
        and all(observed.get(k) for k in ("CTRL", "ALT", "shift_down")),
        "disconnect releases Shift": bool(observed.get("shift_down") and observed.get("shift_up")),
        "mouse movement": bool(observed.get("motion")),
        "three mouse buttons": {1, 2, 3} <= {e[2] for e in events if e[0] == "button_down"}
        and {1, 2, 3} <= {e[2] for e in events if e[0] == "button_up"},
        "mouse wheel": "wheel" in kinds,
        "absolute pointer": bool(observed.get("absolute")),
        "timed F1 repeat": sum(e[0] == "down" and e[1] == "F1" for e in events) == 3,
        "USB completion fences": bool(observed.get("completions")),
    }
    for name, result in checks.items():
        print(name + ": " + ("PASS" if result else "FAIL"), flush=True)
    status.set(
        "\n".join(name + ": " + ("PASS" if result else "FAIL") for name, result in checks.items())
        + "\nYou may close this window."
    )
    button.configure(state=tk.NORMAL, text="Run again")
    root.after(100, poll)


root.after(100, poll)
root.mainloop()
