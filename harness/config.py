"""The configuration of a run, read from the environment (.env)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger("agent")


# --- Configuration ---------------------------------------------------------- #

@dataclass
class ModelProfile:
    """One model as configured in .env (<PROFILE>_ENDPOINT / _API_KEY / _MODEL)."""
    name: str
    endpoint: str
    api_key: str
    model: str
    vision: bool = True
    # Extra fields merged into every request body (<PROFILE>_REQUEST, a JSON
    # object), e.g. {"chat_template_kwargs": {"enable_thinking": false}} to
    # switch a reasoning model's thinking off.
    request: Dict[str, Any] = field(default_factory=dict)
    # Largest screenshot the model accepts (<PROFILE>_IMAGE_MAX_PIXELS); larger
    # ones are scaled down before sending. 0 = no limit.
    image_max_pixels: int = 0

    @property
    def is_decision_model(self) -> bool:
        """Decision models answer typed questions on /v1/systemone and write no text."""
        return self.endpoint.rstrip("/").endswith("/systemone")


@dataclass
class AgentConfig:
    # Planner: a chat model. Decider: a decision model, or any chat model (the
    # planner's own profile when DECIDER_PROFILE is unset or SAME).
    planner: ModelProfile
    decider: ModelProfile
    # Chat model for the planner rounds after a failed step. Those are rare and
    # need reasoning more than speed, so a stronger model or one that thinks
    # can be named here (ESCALATION_PROFILE). None = the planner itself.
    escalation: Optional[ModelProfile] = None
    # Hard caps on one run: executed steps and planner rounds.
    max_steps: int = 60
    max_plan_rounds: int = 15
    planner_max_tokens: int = 1500
    temperature: float = 0.0
    request_timeout: int = 120
    request_retries: int = 2
    decider_timeout: int = 15
    verify_tls: bool = False
    save_debug_screenshots: bool = True
    # When a planner that can see gets the screenshot: 'always' (every round),
    # or 'failed' (only in the round after a failed step).
    planner_images: str = "always"
    # Nobody attends the run (AUTONOMOUS=1): the agent decides everything
    # itself, never asks and never pauses by itself. What it would have asked
    # ends the run and is reported as the reason.
    autonomous: bool = False
    # Run logs (RunLog): whether every request leaves its folder, and where
    # the folders are. An empty directory means logs/ next to this file.
    run_log: bool = False
    run_log_dir: str = ""
    # Below this probability the decider's answer is not acted on and the
    # planner decides instead. 0.5 = the chosen option has no majority.
    decider_min_conf: float = 0.5
    # Ask the decider on every step, also where an exact label match leaves a
    # single candidate (for evaluating a decision model).
    decider_always: bool = False
    # When the decider gets the annotated screenshot: never | retry (only after
    # an answer it was not confident in) | always.
    decider_images: str = "retry"
    # Before replacing a field's content, require the caret next to its label.
    focus_check: bool = True
    # Pause when the pointer is not where the agent left it: a person moved the
    # mouse. Resume in the UI or with the pause key.
    pause_on_pointer_move: bool = True
    # Let a decision model choose navigation clicks itself (which element to
    # click next) instead of asking the planner for every new screen. Used only
    # above decider_nav_conf, and never for a click that commits something.
    decider_navigates: bool = True
    decider_nav_conf: float = 0.8
    # After a click, how long to wait for the screen to do more than highlight
    # the clicked control before it is read (a slow view still has to load).
    click_settle_s: float = 1.5
    batch_entry: bool = True
    batch_settle_s: float = 0.35
    # The fast entry's pixel check: a pixel counts as changed if one of its
    # colour channels differs by more than this many steps of 255. 0 = exact.
    batch_color_tolerance: int = 8
    controls_always: bool = False   # detect controls without text with every full read
    # Window-title substring of the application to drive. Empty = the decider
    # picks the window from the request.
    target_window: str = ""
    # Session continuity: when True, each run() loads the prior requests and
    # their steps from the session file and continues the conversation.
    continue_session: bool = True


# --- Entry point ----------------------------------------------------------- #

def _flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name) or default).strip().lower() not in ("0", "false", "no", "off")


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _load_profile(name: str) -> ModelProfile:
    prof = name.strip().upper()
    endpoint = os.getenv(f"{prof}_ENDPOINT")
    model = os.getenv(f"{prof}_MODEL")
    if not endpoint or not model:
        raise SystemExit(
            f"Missing configuration. Set {prof}_ENDPOINT and {prof}_MODEL in your .env file."
        )
    request: Dict[str, Any] = {}
    raw = os.getenv(f"{prof}_REQUEST")
    if raw:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{prof}_REQUEST is not valid JSON: {exc}")
        if not isinstance(request, dict):
            raise SystemExit(f"{prof}_REQUEST must be a JSON object.")
    return ModelProfile(
        name=prof, endpoint=endpoint, api_key=os.getenv(f"{prof}_API_KEY") or "",
        model=model, vision=_flag(f"{prof}_VISION"), request=request,
        image_max_pixels=_int_env(f"{prof}_IMAGE_MAX_PIXELS", 0, 0),
    )


def _load_config() -> AgentConfig:
    active = os.getenv("ACTIVE_PROFILE")
    if not active:
        raise SystemExit("Missing configuration. Set ACTIVE_PROFILE in your .env file.")
    planner = _load_profile(active)
    if planner.is_decision_model:
        raise SystemExit(
            f"ACTIVE_PROFILE {planner.name} is a decision model: it answers typed questions "
            "and writes no text, so it cannot plan. Pick a chat model as ACTIVE_PROFILE and "
            "set the decision model as DECIDER_PROFILE."
        )
    # DECIDER_PROFILE names the profile that answers the per-step questions: a
    # decision model (endpoint .../v1/systemone) or any chat model. Unset or
    # SAME → the planner's model answers them too, held to the options by a
    # JSON schema (works everywhere, but returns no probabilities).
    name = (os.getenv("DECIDER_PROFILE") or "").strip()
    decider = planner if name.upper() in ("", "SAME") else _load_profile(name)
    name = (os.getenv("ESCALATION_PROFILE") or "").strip()
    escalation = None if name.upper() in ("", "SAME") else _load_profile(name)
    if escalation is not None and escalation.is_decision_model:
        raise SystemExit(f"ESCALATION_PROFILE {escalation.name} is a decision model; "
                         "it has to be a chat model.")
    images = (os.getenv("DECIDER_IMAGES") or "retry").strip().lower()
    if images not in ("never", "retry", "always"):
        log.warning("DECIDER_IMAGES=%r is not one of never/retry/always — using retry.", images)
        images = "retry"
    try:
        min_conf = min(1.0, max(0.0, float(os.getenv("DECIDER_MIN_CONF", "0.5"))))
    except ValueError:
        min_conf = 0.5
    return AgentConfig(
        planner=planner, decider=decider, escalation=escalation,
        max_steps=_int_env("MAX_STEPS", 60, 1),
        max_plan_rounds=_int_env("MAX_PLAN_ROUNDS", 15, 1),
        # Planner output budget. A REASONING model spends it on hidden thinking
        # before the JSON plan; switch thinking off via <PROFILE>_REQUEST or
        # raise this.
        planner_max_tokens=_int_env("PLANNER_MAX_TOKENS", 1500, 200),
        # Per-request timeout (seconds). Raise via .env when a model runs on a
        # contended GPU and responses can take minutes.
        request_timeout=_int_env("REQUEST_TIMEOUT", 120, 10),
        # A decision takes well under a second; one that takes this long is a
        # stalled connection, and the planner decides instead.
        decider_timeout=_int_env("DECIDER_TIMEOUT", 15, 2),
        decider_min_conf=min_conf,
        decider_always=_flag("DECIDER_ALWAYS", "0"),
        decider_images=images,
        focus_check=_flag("FOCUS_CHECK"),
        pause_on_pointer_move=_flag("PAUSE_ON_POINTER_MOVE"),
        decider_navigates=_flag("DECIDER_NAVIGATES"),
        decider_nav_conf=_float_env("DECIDER_NAV_CONF", 0.8),
        click_settle_s=_float_env("CLICK_SETTLE_S", 1.5),
        batch_entry=_flag("BATCH_ENTRY"),
        batch_settle_s=_float_env("BATCH_SETTLE_S", 0.35),
        batch_color_tolerance=_int_env("BATCH_COLOR_TOLERANCE", 8, 0),
        controls_always=(os.getenv("CONTROL_DETECTION") or "needed").strip().lower() == "always",
        target_window=(os.getenv("TARGET_WINDOW") or "").strip(),
        continue_session=_flag("CONTINUE_SESSION"),
        planner_images=("failed" if (os.getenv("PLANNER_IMAGES") or "").strip().lower()
                        == "failed" else "always"),
        autonomous=_flag("AUTONOMOUS", "0"),
        run_log=_flag("RUN_LOG"),
        run_log_dir=(os.getenv("RUN_LOG_DIR") or "").strip(),
    )
