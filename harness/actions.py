"""Mouse and keyboard: the cursor glide, typing, and the executor that carries out
one action."""

from __future__ import annotations

import logging
import math
import os
import platform
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui

from .elements import ElementRegistry
from .paths import _temp_dir

log = logging.getLogger("agent")


# Ceiling for a single wait() call; WAIT_MAX_S=0 removes it entirely. A wait
# blocks the whole agent loop and the UI has nothing to show meanwhile, so the
# cap is not about protecting the run — /api/stop terminates the child process
# mid-sleep, so any wait is interruptible — it is about bounding how long a
# *mistaken* wait (a model passing milliseconds, say) can look like a hang
# before anyone notices. Clipped requests are reported, never silently
# shortened, so the model can wait again rather than assume the time passed.
_WAIT_MAX_S = float(os.getenv("WAIT_MAX_S", "300"))


# Scrolling. One notch of the mouse wheel is 120 units on Windows, and moves a
# view by roughly 100px; pyautogui passes the number it is given on as units,
# so "5" is a twenty-fourth of a notch — a few pixels.
_WHEEL_NOTCH = 120


_PX_PER_NOTCH = 100


_MAX_SCROLL_PAGES = 8   # pages scrolled down in search of a target


# A label this close to the window's lower edge may have its field cut off.
_EDGE_STRIP = 120


# --- DPI awareness --------------------------------------------------------- #

def _enable_dpi_awareness() -> None:
    """Make the process DPI-aware so coordinates are physical pixels."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception as exc:
        log.debug("DPI awareness setup failed: %s", exc)


# --- Action executor ------------------------------------------------------- #

# Cursor glide. Teleporting the pointer to the target makes a run hard to follow
# on screen, so clicks/scrolls ease the cursor over instead.
#
# The knob is a *speed percentage*: 0 = slowest, 100 = instant (the pre-glide
# jump). It maps to the time for a full screen-diagonal traverse, scaled down
# pro rata for shorter hops — so one setting feels consistent whether the target
# is 100px away or on the far monitor. Don't run it too slow: the pointer only
# *passes* over whatever lies on the line to the target, and a slow traverse
# gives hover-triggered menus and tooltips time to open and swallow the click.
_MOUSE_GLIDE = os.getenv("MOUSE_GLIDE", "1").strip().lower() not in ("0", "false", "no", "off")


_MOUSE_GLIDE_SPEED_PCT = float(os.getenv("MOUSE_GLIDE_SPEED_PCT", "30"))  # 1 = slowest .. 100 = near-instant


_MOUSE_GLIDE_MIN_PCT = 1.0  # 0% would read as "stopped", which it never is


_MOUSE_GLIDE_SLOWEST_S = 2.2                                           # full-diagonal traverse at 1%


_MOUSE_GLIDE_FASTEST_S = 0.05                                          # full-diagonal traverse at 100%


_MOUSE_GLIDE_MIN_S = 0.04  # below this an animation is imperceptible — just jump


_MOUSE_GLIDE_MIN_PX = 8                                                # ignore micro-moves


_MOUSE_GLIDE_STEP_S = 0.008                                            # ~120 fps target


_MOUSE_GLIDE_MAX_STEPS = 300


def _glide_speed_pct() -> float:
    """Cursor speed 1-100, re-read from disk on every glide.

    The backend spawns a fresh agent process per task, so an env var alone would
    only take effect on the *next* task. The UI slider writes this file instead,
    which lets a speed change apply mid-run. Missing/unreadable file → env value.
    """
    try:
        raw = (_temp_dir() / "agent_cursor_speed.txt").read_text(encoding="utf-8").strip()
        return max(_MOUSE_GLIDE_MIN_PCT, min(100.0, float(raw)))
    except Exception:
        return max(_MOUSE_GLIDE_MIN_PCT, min(100.0, _MOUSE_GLIDE_SPEED_PCT))


def _glide_full_s(pct: float) -> float:
    """Seconds for a full screen-diagonal traverse at cursor speed `pct`.

    Geometric, not linear: every slider step changes the speed by the same
    *factor* (~1.4x per 10 points), so the control has usable resolution across
    its whole travel. Linear interpolation over a 44x range — either in duration
    or in speed — leaves one half of the slider doing almost nothing.

    The scale is therefore lopsided towards slow, and the 30% default lands at
    ~0.73s per diagonal rather than the midpoint.
    """
    span = 100.0 - _MOUSE_GLIDE_MIN_PCT
    u = (max(_MOUSE_GLIDE_MIN_PCT, min(100.0, pct)) - _MOUSE_GLIDE_MIN_PCT) / span
    return _MOUSE_GLIDE_SLOWEST_S * (_MOUSE_GLIDE_FASTEST_S / _MOUSE_GLIDE_SLOWEST_S) ** u


_KEY_MAP = {
    "strg": "ctrl", "control": "ctrl",
    "meta": "win", "super": "win", "command": "win", "cmd": "win",
    "option": "alt",
    "return": "enter",
    "esc": "esc", "escape": "esc",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "delete": "del",
}


# Actions that manipulate the focused window and must NOT run until an
# application has been deliberately brought to the foreground this run.
# wait is exempt: it touches nothing.
_FOCUS_REQUIRED_ACTIONS = frozenset({
    "click", "double_click", "type_text", "keypress", "scroll", "delete_chars", "drag",
})


# How text is typed. 'unicode' sends every character as itself (Windows
# SendInput with KEYEVENTF_UNICODE), whatever the keyboard layout. 'keys'
# presses the keys of a US layout one by one (pyautogui.write): on a German
# layout that silently drops characters which need AltGr, such as '@', and
# swaps others. 'keys' remains for remote sessions that do not pass Unicode
# input through.
_TYPE_MODE = (os.getenv("TYPE_MODE") or "unicode").strip().lower()


def _type_unicode(text: str, interval: float = 0.012) -> None:
    """Type *text* as Unicode characters, independent of the keyboard layout."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class MOUSEINPUT(ctypes.Structure):   # only so that INPUT has its real size
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    send = ctypes.windll.user32.SendInput
    for ch in text:
        if ch in "\r\n":
            pyautogui.press("enter")
            continue
        if ch == "\t":
            pyautogui.press("tab")
            continue
        raw = ch.encode("utf-16-le")
        for i in range(0, len(raw), 2):   # one or two UTF-16 code units
            unit = int.from_bytes(raw[i:i + 2], "little")
            for flags in (0x0004, 0x0004 | 0x0002):   # KEYEVENTF_UNICODE, + KEYEVENTF_KEYUP
                event = INPUT(type=1, u=_U(ki=KEYBDINPUT(0, unit, flags, 0, None)))
                send(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        time.sleep(interval)


def _type_text(text: str) -> None:
    if _TYPE_MODE == "unicode" and platform.system() == "Windows":
        _type_unicode(text)
    else:
        pyautogui.write(text, interval=0.03)


class ActionExecutor:
    """Pure input dispatcher. No knowledge of screens or LLMs."""

    def __init__(self, width: int, height: int, registry: ElementRegistry):
        self.width = width
        self.height = height
        self.registry = registry
        self.last_click_point: Optional[Tuple[int, int]] = None
        self.last_click_age: int = 0  # iterations since last click, for marker fade
        # Window-focus gating + persistence. `focused_once` guards input actions
        # until a focus has succeeded this run; `target_window` is the substring
        # of the last successfully-focused window, persisted across follow-up
        # requests in the same conversation (see save_session/load_session).
        self.focused_once: bool = False
        self.target_window: Optional[str] = None
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.0

    def _resolve_point(self, args: Dict[str, Any]) -> Tuple[Optional[Tuple[int, int]], str]:
        eid = args.get("element_id")
        if eid:
            # Models copy the id straight from the prompt line '[e75] ...' —
            # tolerate the brackets instead of burning a round on an error.
            eid = str(eid).strip().strip("[]")
            el = self.registry.get(eid)
            if el is None:
                return None, (
                    f"error: element_id '{eid}' not found (may have been evicted). "
                    "Use an id from the LATEST element list or fall back to x/y."
                )
            return (el.center_x, el.center_y), ""
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return None, "error: no element_id or coordinates provided"
        return (int(x), int(y)), ""

    def _clamp(self, x: int, y: int) -> Tuple[int, int]:
        x = max(2, min(self.width - 2, int(x)))
        y = max(2, min(self.height - 2, int(y)))
        return x, y

    def _glide(self, x: int, y: int) -> None:
        """Ease the cursor to (x, y) instead of letting it teleport there.

        Cosmetic only — the caller still issues its click at the same absolute
        coordinates, so a glide that is skipped or fails changes nothing about
        where the click lands. Hence the blanket except: a movement problem must
        never cost us the action itself.
        """
        if not _MOUSE_GLIDE:
            return
        try:
            cx, cy = pyautogui.position()
            dist = math.hypot(x - cx, y - cy)
            if dist < _MOUSE_GLIDE_MIN_PX:
                return
            diag = math.hypot(self.width, self.height) or 1.0
            duration = _glide_full_s(_glide_speed_pct()) * min(1.0, dist / diag)
            if duration < _MOUSE_GLIDE_MIN_S:
                return  # a glide this brief looks identical to the jump
            # Stepped by hand rather than via moveTo(duration=...): pyautogui
            # sleeps MINIMUM_SLEEP (0.05s) per step, which yields ~7 frames over
            # a full-screen move — still visibly a series of jumps.
            steps = max(2, min(_MOUSE_GLIDE_MAX_STEPS, int(duration / _MOUSE_GLIDE_STEP_S)))
            for i in range(1, steps + 1):
                t = i / steps
                e = 1.0 - (1.0 - t) * (1.0 - t)  # ease-out quad
                pyautogui.moveTo(int(cx + (x - cx) * e), int(cy + (y - cy) * e))
                if i < steps:
                    time.sleep(duration / steps)
            pyautogui.moveTo(x, y)  # land exactly, whatever the rounding did
        except Exception:
            log.debug("glide failed, falling back to instant move", exc_info=True)

    def execute(self, action_type: str, args: Dict[str, Any]) -> str:
        try:
            # Gate: never touch the screen until a target application has been
            # deliberately focused this run. Prevents acting on whatever window
            # happened to be frontmost (e.g. the agent's own UI).
            if action_type in _FOCUS_REQUIRED_ACTIONS and not self.focused_once:
                return ("error: no application has been brought to the front yet, so "
                        "nothing is clicked or typed")

            if action_type in ("click", "double_click"):
                point, err = self._resolve_point(args)
                if point is None:
                    return err
                x, y = self._clamp(*point)
                button = args.get("button", "left")
                self._glide(x, y)
                if action_type == "double_click":
                    pyautogui.doubleClick(x, y)
                else:
                    pyautogui.click(x, y, button=button)
                self.last_click_point = (x, y)
                self.last_click_age = 0
                time.sleep(0.4)
                return f"clicked ({x},{y})"

            if action_type == "drag":
                x, y = self._clamp(int(args["x"]), int(args["y"]))
                tx, ty = self._clamp(int(args["to_x"]), int(args["to_y"]))
                self._glide(x, y)
                pyautogui.mouseDown(x, y)
                try:
                    # In steps, whatever the cursor speed is set to: an
                    # application takes a button held and a jump to another
                    # place for a click, not for a drag.
                    time.sleep(0.15)
                    steps = max(8, min(40, int(math.hypot(tx - x, ty - y) / 15)))
                    for i in range(1, steps + 1):
                        pyautogui.moveTo(int(x + (tx - x) * i / steps),
                                         int(y + (ty - y) * i / steps))
                        time.sleep(0.015)
                    pyautogui.moveTo(tx, ty)
                    time.sleep(0.15)
                finally:
                    pyautogui.mouseUp()
                self.last_click_point = (tx, ty)
                self.last_click_age = 0
                time.sleep(0.4)
                return f"dragged ({x},{y}) to ({tx},{ty})"

            if action_type == "type_text":
                text = args.get("text", "")
                _type_text(text)
                time.sleep(0.2)
                return f"typed {len(text)} chars"

            if action_type == "keypress":
                keys = [_KEY_MAP.get(k.lower(), k.lower()) for k in args.get("keys", [])]
                if not keys:
                    return "error: no keys"
                if len(keys) == 1:
                    time.sleep(0.1)
                    pyautogui.press(keys[0])
                    time.sleep(0.1)
                else:
                    time.sleep(0.2)
                    pyautogui.hotkey(*keys)
                    time.sleep(0.2)
                return f"pressed {'+'.join(keys)}"

            if action_type == "scroll":
                x = args.get("x", self.width // 2)
                y = args.get("y", self.height // 2)
                scroll_clicks = int(args.get("scroll_clicks", args.get("scroll_y", 0)))
                sx, sy = self._clamp(x, y)
                self._glide(sx, sy)
                pyautogui.moveTo(sx, sy)
                pyautogui.scroll(scroll_clicks)  # pyautogui: positive=up, negative=down
                return f"scrolled {scroll_clicks} clicks ({'up' if scroll_clicks > 0 else 'down'})"

            if action_type == "wait":
                try:
                    secs = float(args.get("seconds", 1.5))
                except (TypeError, ValueError):
                    return "error: 'seconds' must be a number of seconds (not milliseconds)"
                if not math.isfinite(secs):
                    return "error: 'seconds' must be a finite number"
                secs = max(0.0, secs)
                slept = min(secs, _WAIT_MAX_S) if _WAIT_MAX_S > 0 else secs
                if slept >= 5:
                    # Announce it up front: the loop is about to go silent, and a
                    # quiet timeline is indistinguishable from a hung agent.
                    log.info("   waiting %.1fs ...", slept)
                time.sleep(slept)
                if slept < secs:
                    # Never report the requested time as elapsed: this string is
                    # what the model and the UI timeline both read.
                    return (
                        f"waited {slept:.1f}s — the requested {secs:.1f}s exceeds the "
                        f"{_WAIT_MAX_S:.0f}s limit for one call. NOTE the parameter is "
                        "SECONDS, not milliseconds. Call wait again if you need longer."
                    )
                return f"waited {slept:.1f}s"

            if action_type == "delete_chars":
                count = max(1, min(int(args.get("count", 1)), 500))
                # from_caret: what is deleted is the text just typed, and the
                # text cursor still sits right behind it - wherever in the
                # field that is. End would move it away.
                if not args.get("from_caret"):
                    pyautogui.press("end")
                    time.sleep(0.1)
                for _ in range(count):
                    pyautogui.press("backspace")
                    time.sleep(0.02)
                return (f"pressed backspace {count} times" if args.get("from_caret")
                        else f"pressed End then backspace {count} times")

            return f"error: unknown action {action_type}"

        except pyautogui.FailSafeException:
            return "error: failsafe triggered"
        except Exception as exc:
            log.exception("Action error")
            return f"error: {exc}"

    def tick_click_age(self) -> None:
        """Called once per iteration. After 2 iterations, forget the click marker."""
        self.last_click_age += 1
        if self.last_click_age >= 2:
            self.last_click_point = None

    # --- Window management helpers (Windows) ------------------------------ #

    @staticmethod
    def _list_windows() -> str:
        if platform.system() != "Windows":
            return "error: list_windows is only supported on Windows"
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        titles: List[str] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    titles.append(buf.value)
            return True

        user32.EnumWindows(enum_cb, 0)
        if not titles:
            return "(no visible windows found)"
        return "Visible windows:\n" + "\n".join(f"  - {t}" for t in titles)

    def _focus_window(self, title_substr: str) -> str:
        if platform.system() != "Windows":
            return "error: focus_window is only supported on Windows"
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        title_lower = title_substr.lower()
        target_hwnd = None

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            nonlocal target_hwnd
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if title_lower in buf.value.lower():
                        target_hwnd = hwnd
                        return False
            return True

        user32.EnumWindows(enum_cb, 0)
        if target_hwnd is None:
            return f"error: no window matching '{title_substr}' found"
        SW_RESTORE = 9
        if user32.IsIconic(target_hwnd):
            user32.ShowWindow(target_hwnd, SW_RESTORE)
        user32.SetForegroundWindow(target_hwnd)
        time.sleep(0.5)
        length = user32.GetWindowTextLengthW(target_hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(target_hwnd, buf, length + 1)
        # Record the focus so input actions are unblocked and the target
        # persists across follow-up requests in this conversation.
        self.focused_once = True
        self.target_window = title_substr
        return f"focused '{buf.value}'"
