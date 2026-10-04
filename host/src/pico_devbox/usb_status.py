"""Versioned USB telemetry; transfer completion is not OS input consumption."""

import struct
from dataclasses import dataclass

from .protocol import DevBoxError


@dataclass(frozen=True)
class HIDStatus:
    accepted: int
    submitted: int
    completed: int
    last_completion_age_ms: int | None
    set_idle_count: int
    set_protocol_count: int
    protocol: int
    endpoint: int
    lifetime_completed: int
    lifetime_last_completion_age_ms: int | None


@dataclass(frozen=True)
class USBStatus:
    mounted: bool
    suspended: bool
    bus_resets: int
    set_configuration_count: int
    epoch: int
    uptime_ms: int
    first_configured_ms: int | None
    first_network_ready_ms: int | None
    hid: tuple[HIDStatus, ...]

    @classmethod
    def decode(cls, data: bytes) -> "USBStatus":
        if len(data) != 156:
            raise DevBoxError(1, "USB telemetry requires updated firmware")
        words = struct.unpack("<39I", data)
        if words[0] != 1 or words[1] > 1 or words[2] > 1:
            raise DevBoxError(1, "invalid USB telemetry")
        interfaces = []
        for i in range(3):
            a, s, c, age, idle, protocol, value, endpoint, total, total_age = words[
                9 + i * 10 : 19 + i * 10
            ]
            if endpoint != (0x81, 0x82, 0x84)[i]:
                raise DevBoxError(1, "invalid HID endpoint")
            interfaces.append(
                HIDStatus(
                    a,
                    s,
                    c,
                    None if age == 0xFFFFFFFF else age,
                    idle,
                    protocol,
                    value,
                    endpoint,
                    total,
                    None if total_age == 0xFFFFFFFF else total_age,
                )
            )
        return cls(
            bool(words[1]),
            bool(words[2]),
            words[3],
            words[4],
            words[5],
            words[6],
            words[7] or None,
            words[8] or None,
            tuple(interfaces),
        )


@dataclass(frozen=True)
class ConsoleStatus:
    """The GPIO UART bridge to the target console (protocol op 9)."""

    target_powered: bool
    client_attached: bool
    rx_level: int
    rx_bytes: int
    rx_errors: int
    dropped: int
    tx_queued: int

    @classmethod
    def decode(cls, data: bytes) -> "ConsoleStatus":
        if len(data) != 32:
            raise DevBoxError(1, "console telemetry requires updated firmware")
        version, powered, client, level, rx, errors, dropped, queued = struct.unpack("<8I", data)
        if version != 1 or powered > 1 or client > 1 or level > 1:
            raise DevBoxError(1, "invalid console telemetry")
        return cls(bool(powered), bool(client), level, rx, errors, dropped, queued)
