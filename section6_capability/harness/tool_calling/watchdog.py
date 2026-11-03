"""Restart the Lua bridge inside a still-running VBA-RR without losing the game."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from pathlib import Path

from tool_calling.frames import current_frame


def bridge_alive(bridge: Path, timeout: float = 3.0) -> bool:
    return current_frame(bridge, timeout=timeout) is not None


WM_GETTEXT = 0x000D
WM_CLOSE = 0x0010
WM_GETTEXTLENGTH = 0x000E


def _control_text(user32, hwnd) -> str:
    user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint,
                                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.wintypes.LPARAM
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.addressof(buffer))
    return buffer.value


def lua_window_text(helpers) -> list[dict]:
    dialog = helpers.find_lua_dialog(timeout=2)
    if not dialog:
        return []
    return [{"class": cls, "control": control,
             "text": _control_text(helpers.user32, hwnd)[-4000:]}
            for hwnd, cls, _text, control in helpers.dump_dialog_children(dialog)]


def restart_bridge(helpers, game, bridge: Path, wait_seconds: float = 45.0) -> tuple[bool, list[dict]]:
    process = getattr(game, "process", None)
    if process is None or process.poll() is not None:
        return False, []
    try:
        captured = lua_window_text(helpers)
    except Exception as exc:
        captured = [{"capture_error": f"{type(exc).__name__}: {exc}"}]
    for name in ("cmd.txt", "screenshot.req"):
        try:
            (bridge / name).unlink()
        except FileNotFoundError:
            pass
    def wait_alive(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if bridge_alive(bridge, timeout=2.0):
                return True
            time.sleep(1.0)
        return False

    dialog = helpers.find_lua_dialog(timeout=2)
    if dialog:
        buttons = {text.strip().lower(): hwnd
                   for hwnd, cls, text, _control in helpers.dump_dialog_children(dialog)
                   if cls == "Button"}
        for label in ("restart", "run"):
            if label in buttons:
                helpers.user32.SendMessageW(buttons[label], helpers.BM_CLICK, 0, 0)
                if wait_alive(15.0):
                    return True, captured
                break
        helpers.user32.PostMessageW(dialog, WM_CLOSE, 0, 0)
        time.sleep(1.0)
    helpers.auto_load_lua_script(pid=process.pid)
    if wait_alive(wait_seconds):
        return True, captured
    return False, captured
