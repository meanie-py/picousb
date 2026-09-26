"""Paced authenticated UDP echo diagnostic; never serves storage or logs payloads."""

import argparse
import asyncio
import json
import logging
import secrets
import socket
import struct
import time
from pathlib import Path

from config_block import parse_env
from pico_devbox import DevBox
from pico_devbox.protocol import DevBoxError, Op
from profile_storage import timing

HEADER = struct.Struct("<4sI16sI")


class Probe(asyncio.DatagramProtocol):
    def __init__(self, session: int, cookie: bytes) -> None:
        self.session, self.cookie = session, cookie
        self.sent: dict[int, float] = {}
        self.received: set[int] = set()
        self.rtt: list[float] = []
        self.arrival_gaps: list[float] = []
        self.last_arrival: float | None = None
        self.origin = time.perf_counter()
        self.slow: list[dict[str, float]] = []
        self.gaps: list[dict[str, float]] = []
        self.highest = 0
        self.duplicates = self.reordered = self.invalid = self.socket_errors = 0

    def packet(self, seq: int) -> bytes:
        return HEADER.pack(b"PDU1", self.session, self.cookie, seq) + b"\x5a" * (1200 - HEADER.size)

    def datagram_received(self, data: bytes, addr: object) -> None:
        if len(data) != 1200:
            self.invalid += 1
            return
        _, _, _, seq = HEADER.unpack_from(data)
        if seq not in self.sent or data != self.packet(seq):
            self.invalid += 1
            return
        if seq in self.received:
            self.duplicates += 1
            return
        now = time.perf_counter()
        rtt = now - self.sent[seq]
        self.rtt.append(rtt)
        if rtt >= 0.25:
            self.slow.append(
                {
                    "sent_at_s": round(self.sent[seq] - self.origin, 3),
                    "rtt_ms": round(rtt * 1000, 3),
                }
            )
        if self.last_arrival is not None:
            gap = now - self.last_arrival
            self.arrival_gaps.append(gap)
            if gap >= 0.25:
                self.gaps.append(
                    {
                        "at_s": round(self.last_arrival - self.origin, 3),
                        "gap_ms": round(gap * 1000, 3),
                    }
                )
        self.last_arrival = now
        self.received.add(seq)
        self.reordered += seq < self.highest
        self.highest = max(seq, self.highest)

    def error_received(self, exc: Exception) -> None:
        self.socket_errors += 1


async def run(args: argparse.Namespace) -> None:
    values = parse_env(args.config.read_text(encoding="utf-8-sig"))
    token = values.pop("DEVICE_TOKEN", "")
    values.clear()
    async with DevBox(args.url, token=token) as box:
        cookie = secrets.token_bytes(16)
        reply = await box.request(Op.UDP_PROBE, int((args.seconds + 5) * 1000), payload=cookie)
        if reply.a != 1 or reply.b != 8081 or len(reply.payload) != 20 or box.ws is None:
            raise ValueError("unsupported probe")
        probe = Probe(box.session, cookie)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: probe, remote_addr=(box.ws.remote_address[0], reply.b), family=socket.AF_INET
        )
        tcp: list[float] = []
        tcp_slow: list[dict[str, float]] = []
        lag: list[float] = []
        stop = asyncio.Event()
        started = time.perf_counter()
        probe.origin = started

        async def control() -> None:
            while not stop.is_set():
                start = time.perf_counter()
                if args.tcp_bytes:
                    try:
                        await box.request(Op.STATUS, payload=b"P" * args.tcp_bytes)
                    except DevBoxError as error:
                        if error.code != 8:
                            raise
                    else:
                        raise ValueError("expected argument rejection")
                else:
                    await box.status()
                elapsed = time.perf_counter() - start
                tcp.append(elapsed)
                if elapsed >= 0.25:
                    tcp_slow.append(
                        {"at_s": round(start - started, 3), "ms": round(elapsed * 1000, 3)}
                    )
                await asyncio.sleep(getattr(args, "tcp_interval", 0.1))

        task = asyncio.create_task(control())
        try:
            seq = 0
            target = started
            while time.perf_counter() - started < args.seconds:
                if task.done():
                    await task
                now = time.perf_counter()
                lag.append(max(0, now - target))
                seq += 1
                probe.sent[seq] = now
                transport.sendto(probe.packet(seq))
                # Pace relative to actual send time; never burst to catch up.
                target = now + 1 / args.pps
                # Windows asyncio timers can quantize short sleeps to ~16 ms.
                # Python's time.sleep uses a high-resolution wait there; keep
                # it off the event loop so receive/control processing continues.
                await asyncio.to_thread(time.sleep, max(0, target - time.perf_counter()))
            sending_seconds = time.perf_counter() - started
            stop.set()  # No new TCP samples after the UDP sending interval.
            await asyncio.sleep(2)  # bounded grace period for delayed replies
            await task
            stats = await box.request(Op.UDP_PROBE)
            if stats.a != 1 or stats.b != 8081 or len(stats.payload) != 20:
                raise ValueError("invalid probe stats")
            firmware = dict(
                zip(
                    ("valid_received", "echo_submitted", "invalid", "rate_dropped", "send_failed"),
                    struct.unpack("<5I", stats.payload),
                    strict=True,
                )
            )
            print(
                json.dumps(
                    {
                        "sent": seq,
                        "received": len(probe.received),
                        "missing_after_grace": seq - len(probe.received),
                        "actual_pps": round(seq / sending_seconds, 2),
                        "duplicates": probe.duplicates,
                        "reordered": probe.reordered,
                        "invalid": probe.invalid,
                        "socket_errors": probe.socket_errors,
                        "udp_rtt": timing(probe.rtt),
                        "udp_slow_packets": probe.slow,
                        "udp_gap_intervals": probe.gaps,
                        "udp_arrival_gaps": timing(probe.arrival_gaps),
                        "sender_scheduling_lag": timing(lag),
                        "tcp_status_rtt": timing(tcp),
                        "tcp_payload_bytes": args.tcp_bytes,
                        "tcp_interval_s": getattr(args, "tcp_interval", 0.1),
                        "tcp_slow_intervals": tcp_slow,
                        "firmware": firmware,
                    }
                ),
                flush=True,
            )
        finally:
            transport.close()
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://picousb.local:8080/ws")
    parser.add_argument("--config", type=Path, default=Path(".env.flash"))
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--pps", type=int, default=100)
    parser.add_argument("--tcp-bytes", type=int, choices=(0, 1200, 16384), default=0)
    parser.add_argument("--tcp-interval", type=float, choices=(0.0, 0.1), default=0.1)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 45 or not 1 <= args.pps <= 400:
        parser.error("duration must be 1..45 seconds and rate 1..400 packets/s")
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Probe stopped.")
    except Exception:
        print("Probe failed; check firmware and connectivity locally. Details suppressed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
