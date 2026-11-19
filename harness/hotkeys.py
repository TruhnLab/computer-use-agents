"""Stopping and pausing a run: the global keys and the two exceptions that carry
a stop or a take-over out of the run loop."""

from __future__ import annotations

import logging
import platform
import re
import threading
from typing import Dict, Optional, Tuple

from .paths import _pause_flag_path

log = logging.getLogger("agent")


# --- Operator controls: stop and pause without the mouse ------------------- #
#
# The agent moves the real pointer, so the person who wants to stop it would
# have to win the pointer back first and then hit a button in a window that
# may be covered. Two global keys work from anywhere instead (registered in the
# agent process, Windows only): STOP_KEY ends the run before the next input
# action, PAUSE_KEY toggles the pause flag. The flag is a file — the mechanism
# of the cursor-speed file — so the UI can set it as well: the agent is paused
# for as long as the file exists, and the file's text is the reason shown.
# A second file is the UI's stop: it has the effect of the stop key, so a run
# stopped from the UI can still say which step it was in.

_HOTKEY_VK = {
    "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "home": 0x24, "end": 0x23,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}


_HOTKEY_MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}


_WM_HOTKEY = 0x0312


def _parse_hotkey(spec: str) -> Optional[Tuple[int, int]]:
    """'pause' / 'ctrl+alt+s' → (modifiers, virtual key); None if empty or unknown."""
    keys = [k.strip().lower() for k in re.split(r"[+\s]+", spec or "") if k.strip()]
    if not keys or keys[-1] not in _HOTKEY_VK or any(k not in _HOTKEY_MODS for k in keys[:-1]):
        return None
    mods = 0
    for k in keys[:-1]:
        mods |= _HOTKEY_MODS[k]
    return mods, _HOTKEY_VK[keys[-1]]


class _Hotkeys(threading.Thread):
    """Listens for the stop and the pause key. Windows delivers a hotkey to the
    thread that registered it, so registration and message loop both live in
    run(). `stop_requested` is polled by the agent; the pause key only toggles
    the flag file."""

    def __init__(self, stop_key: str, pause_key: str):
        super().__init__(daemon=True, name="agent-hotkeys")
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.active: Dict[str, str] = {}   # 'stop' / 'pause' → key, for those registered
        self._keys = {1: ("stop", stop_key), 2: ("pause", pause_key)}

    def run(self) -> None:
        if platform.system() != "Windows":
            self.ready.set()
            return
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        for hotkey_id, (name, spec) in self._keys.items():
            parsed = _parse_hotkey(spec)
            # 0x4000 = MOD_NOREPEAT: a held key counts once.
            if parsed and user32.RegisterHotKey(None, hotkey_id, parsed[0] | 0x4000, parsed[1]):
                self.active[name] = spec
            elif spec:
                log.warning("Could not register the %s key %r (unknown key, or taken by "
                            "another program).", name, spec)
        self.ready.set()
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != _WM_HOTKEY:
                continue
            if msg.wParam == 1:
                self.stop_requested.set()
            elif msg.wParam == 2:
                self.toggle_pause()

    @staticmethod
    def toggle_pause() -> None:
        flag = _pause_flag_path()
        try:
            if flag.exists():
                flag.unlink()
            else:
                flag.write_text("paused with the pause key", encoding="utf-8")
        except OSError as exc:
            log.debug("pause flag toggle failed: %s", exc)


class _StopRequested(BaseException):
    """The operator pressed the stop key. A BaseException, so that no
    'except Exception' on the way up can swallow it."""


class _TakenOver(BaseException):
    """The agent was paused and resumed: the user may have worked on the
    screen, so the step in progress and the remaining plan no longer hold."""
