"""The emulator's current frame from the Lua bridge."""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

from PIL import Image


def decode_gd(data: bytes) -> Image.Image | None:
    if len(data) < 11 or data[:2] != b"\xff\xfe":
        return None
    width, height = int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:6], "big")
    size = width * height * 4
    if not width or not height or len(data) < size + 6:
        return None
    image = Image.frombuffer("RGB", (width, height), data[-size:], "raw", "XRGB", 0, 1)
    if (width, height) == (256, 224):
        image = image.crop((48, 40, 208, 184))
    return image


def current_frame(bridge_dir: Path, timeout: float = 1.0) -> Image.Image | None:
    screen = bridge_dir / "screen.gd"
    try:
        screen.unlink()
    except FileNotFoundError:
        pass
    (bridge_dir / "screenshot.req").write_text("1", encoding="ascii")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            image = decode_gd(screen.read_bytes())
            if image is not None:
                return image.resize((160, 144), Image.NEAREST)
        except OSError:
            pass
        time.sleep(0.005)
    return None


def data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
