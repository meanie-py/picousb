import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from pico_devbox.protocol import HEADER, MAX_PAYLOAD, DevBoxError, Message, Op

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("config_block", ROOT / "tools/config_block.py")
assert spec and spec.loader
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def test_protocol_roundtrip() -> None:
    for n in (0, 1, 512, 1024, MAX_PAYLOAD):
        m = Message(Op.WRITE, 123, 9, 65535, 1, bytes(n))
        assert Message.decode(m.encode()) == m


@pytest.mark.parametrize(
    "data", [b"", "text", b"PDB0" + bytes(24), bytes(28), Message(1, 1).encode() + b"x"]
)
def test_malformed(data: bytes | str) -> None:
    with pytest.raises(DevBoxError):
        Message.decode(data)


def test_size_limit_and_reserved() -> None:
    with pytest.raises(DevBoxError):
        Message(1, 1, payload=bytes(MAX_PAYLOAD + 1)).encode()
    frame = bytearray(Message(1, 1).encode())
    frame[6] = 1
    with pytest.raises(DevBoxError):
        Message.decode(bytes(frame))
    assert HEADER.size == 28


def values() -> dict[str, str]:
    return {
        "WIFI_SSID": "synthetic",
        "WIFI_PASSWORD": "synthetic-password",
        "DEVICE_TOKEN": "synthetic-test-token",
        "UNKNOWN": "abc=def",
    }


def test_config_roundtrip() -> None:
    v = values()
    text = "# synthetic fixture\n\n" + "\n".join(f"{k}={val}" for k, val in v.items())
    assert config.parse_env(text) == v
    block = config.encode(v)
    assert len(block) == 4096
    assert config.decode(block) == v
    uf2 = config.uf2(block)
    assert len(uf2) == 17 * 512
    assert uf2[:512] == config.workaround_block()
    for i in range(16):
        magic, _, flags, address, size, index, count, family = struct.unpack_from(
            "<8I", uf2, (i + 1) * 512
        )
        assert (magic, flags, address, size, index, count, family) == (
            0x0A324655,
            0x2000,
            0x103FF000 + i * 256,
            256,
            i,
            16,
            0xE48BFF59,
        )


@pytest.mark.parametrize("offset", range(96))
def test_config_corruption(offset: int) -> None:
    b = bytearray(config.encode(values()))
    b[offset] ^= 1
    with pytest.raises(config.ConfigError):
        config.decode(bytes(b))


@pytest.mark.parametrize(
    "text",
    [
        "WIFI_SSID=only",
        "x=bad",
        "=bad",
        "not a line",
        "WIFI_SSID=a\nWIFI_SSID=b",
        "WIFI_SSID=a\x00b",
    ],
)
def test_invalid_env(text: str) -> None:
    with pytest.raises(config.ConfigError):
        config.parse_env(text)


def test_config_capacity() -> None:
    v = values()
    v["EXTRA"] = "x" * 4096
    with pytest.raises(config.ConfigError):
        config.encode(v)
    v = values()
    del v["DEVICE_TOKEN"]
    assert config.decode(config.encode(v)) == v


def test_cli_does_not_echo_secrets(tmp_path: Path) -> None:
    secret = "SYNTHETIC-DO-NOT-ECHO"
    source = tmp_path / "synthetic-input.txt"
    source.write_text(secret)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/config_block.py"),
            "--input",
            str(source),
            "--output",
            str(tmp_path / "config.uf2"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_combined_uf2_preserves_workaround_and_protects_config() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from provision import combine

        synthetic = config.encode(values())
        block = bytearray(config.uf2(synthetic)[512:1024])
        struct.pack_into("<I", block, 12, 0x10000000)
        firmware = config.workaround_block() + block
        combined = combine(firmware, synthetic)
        assert len(combined) == 18 * 512
        assert combined[:512] == config.workaround_block()
        for i in range(17):
            assert struct.unpack_from("<II", combined, (i + 1) * 512 + 20) == (i, 17)
        with pytest.raises(ValueError):
            combine(config.uf2(synthetic), synthetic)
        with pytest.raises(ValueError):
            combine(firmware + block, synthetic)
    finally:
        sys.path.pop(0)
