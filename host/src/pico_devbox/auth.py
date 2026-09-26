"""Local, bounded credential loading. Errors never include file data or paths."""

import getpass
import os
import sys
from pathlib import Path

from .protocol import DevBoxError


def read_token_file(path: str | Path) -> str:
    try:
        with Path(path).open("rb") as source:
            data = source.read(131)
        if len(data) > 130:
            raise ValueError
        token = data.rstrip(b"\r\n")
        if not 16 <= len(token) <= 128 or b"\n" in token or b"\r" in token or b"\0" in token:
            raise ValueError
        return token.decode("utf-8")
    except Exception:
        raise DevBoxError(2, "cannot load device token file") from None


def cli_token() -> str:
    path = os.environ.get("PICO_DEVICE_TOKEN_FILE")
    if path is not None:
        return read_token_file(path)
    token = os.environ.get("PICO_DEVICE_TOKEN")
    if token is not None:
        return token
    if not sys.stdin.isatty():
        raise DevBoxError(2, "non-interactive authentication requires token configuration")
    return getpass.getpass("Device token (blank if disabled): ")
