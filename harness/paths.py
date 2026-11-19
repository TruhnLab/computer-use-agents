"""Where the harness keeps its files: the repository folder and the files in the
temporary folder that the backend and the agent share."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# The repository folder: models/ and logs/ lie in it.
ROOT = Path(__file__).resolve().parent.parent


def _pause_flag_path() -> Path:
    return Path(os.environ.get("TEMP", tempfile.gettempdir())) / "agent_pause.flag"


def _stop_flag_path() -> Path:
    return Path(os.environ.get("TEMP", tempfile.gettempdir())) / "agent_stop.flag"


def _temp_dir() -> Path:
    return Path(os.environ.get("TEMP", tempfile.gettempdir()))


def _session_path() -> Path:
    return _temp_dir() / "agent_session.json"


def _debug_screenshot_path() -> Path:
    return _temp_dir() / "agent_screenshot_debug.png"


def _raw_screenshot_path() -> Path:
    return _temp_dir() / "agent_screenshot_raw.png"
