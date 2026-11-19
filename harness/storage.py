"""What a run leaves on disk: the session file that carries a conversation from
one request to the next, and the run log."""

from __future__ import annotations

import csv
import datetime
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from .paths import (
    ROOT,
    _debug_screenshot_path,
    _pause_flag_path,
    _raw_screenshot_path,
    _session_path,
    _stop_flag_path,
)

log = logging.getLogger("agent")


# --- Session persistence (conversation continuity) ------------------------- #
#
# A conversation is a sequence of user requests that share context. Because the
# backend runs each task in a fresh subprocess, continuity is carried on disk:
# the earlier requests, the steps carried out for them and the window that was
# driven. The planner gets them with every round, which is enough to "continue
# the whole conversation". Startup and the in-app "New conversation" control
# call clear_session().

def load_session() -> Dict[str, Any]:
    """Return the persisted session dict, or {} if none / unreadable."""
    try:
        return json.loads(_session_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_session(data: Dict[str, Any]) -> None:
    try:
        _session_path().write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        log.debug("session save failed: %s", exc)


def clear_session() -> None:
    """Wipe conversation context, the last screenshots and left-over pause/stop flags.

    Called on startup (always a fresh conversation) and by the in-app
    'New conversation' control during usage.
    """
    for p in (_session_path(), _debug_screenshot_path(), _raw_screenshot_path(),
              _pause_flag_path(), _stop_flag_path()):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("clear_session: could not remove %s (%s)", p, exc)


_RUN_COLUMNS = ("started", "folder", "outcome", "seconds", "steps", "planner_calls",
                "decider_calls", "version", "request", "result")


_version_cache: Optional[str] = None


def _harness_version() -> str:
    """The commit this file runs from ('dbdacb9', with '-dirty' when it has
    local changes), so that a log read weeks later says which harness wrote
    it. Empty without git."""
    global _version_cache
    if _version_cache is None:
        try:
            _version_cache = subprocess.run(
                ["git", "describe", "--always", "--dirty"],
                cwd=ROOT, capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            _version_cache = ""
    return _version_cache


class RunLog:
    """What one request leaves on disk: a folder of its own under the log
    directory, named by its start time, with

      run.log          every log line of the run, as on the console
      planner_NN.txt   what the planner was sent in round NN, and its reply
      planner_NN.png   the screen in that round

    and one line in runs.csv next to the folders: when, how it ended, how
    long it took, what was asked and what was answered.

    Nothing is removed: the runs are collected, to go back to.

    The log holds what the screen showed, so on a real system it holds
    patient data: it stays on this machine, and RUN_LOG=0 writes none.

    Nothing here may stop a run: a log that cannot be written is reported
    once, and the run goes on without it."""

    def __init__(self, directory: str, enabled: bool) -> None:
        self.dir: Optional[Path] = None
        self._handler: Optional[logging.Handler] = None
        self._rounds = 0
        self._started = datetime.datetime.now()
        if not enabled:
            return
        try:
            root = Path(directory) if directory else ROOT / "logs"
            name = self._started.strftime("%Y-%m-%d_%H-%M-%S")
            folder, n = root / name, 1
            while folder.exists():           # a second run in the same second
                n += 1
                folder = root / f"{name}_{n}"
            folder.mkdir(parents=True)
            handler = logging.FileHandler(folder / "run.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))
            log.addHandler(handler)
            self.dir, self._handler = folder, handler
        except OSError as exc:
            log.warning("no run log: %s", exc)

    def planner_round(self, model: str, sent: str, reply: str,
                      screen: Optional[Image.Image]) -> None:
        if self.dir is None:
            return
        self._rounds += 1
        try:
            (self.dir / f"planner_{self._rounds:02d}.txt").write_text(
                f"MODEL: {model}\n\n{sent}\n\n===== REPLY =====\n{reply}\n", encoding="utf-8")
            if screen is not None:
                screen.save(self.dir / f"planner_{self._rounds:02d}.png", format="PNG")
        except OSError as exc:
            log.debug("planner round not written to the run log: %s", exc)

    def close(self, request: str, outcome: str, result: str, steps: int,
              calls: Dict[str, int]) -> None:
        if self._handler is not None:
            log.removeHandler(self._handler)
            self._handler.close()
            self._handler = None
        if self.dir is None:
            return
        seconds = (datetime.datetime.now() - self._started).total_seconds()
        try:
            index = self.dir.parent / "runs.csv"
            new = not index.exists()
            # With a byte order mark at its start, so that a spreadsheet reads
            # umlauts in a request as they were typed.
            with open(index, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as fh:
                writer = csv.writer(fh)
                if new:
                    writer.writerow(_RUN_COLUMNS)
                writer.writerow([
                    self._started.isoformat(timespec="seconds"), self.dir.name, outcome,
                    round(seconds, 1), steps, calls.get("planner", 0), calls.get("decider", 0),
                    _harness_version(), " ".join(request.split()), " ".join(result.split())])
        except OSError as exc:
            log.warning("the run is not listed in runs.csv: %s", exc)


def _run_outcome(result: Dict[str, Any]) -> Tuple[str, str]:
    """How a run ended, for runs.csv: a word, and what the user was told."""
    if result.get("question"):
        return "question", result["question"]
    last = result["actions"][-1] if result.get("actions") else {}
    if last.get("action") == "task_complete":
        return ("done, with open points" if last.get("open") else "done"), last.get("claim", "")
    if last.get("unanswered"):
        return "unanswered question", last["unanswered"]
    error = last.get("error", "")
    return ("stopped" if "stopped by the user" in error else "aborted"), error
