#!/usr/bin/env python3
"""
Entry point of the computer use agent. The code is in the package harness/;
this file starts it from the command line and holds the names that the
backend and the evaluation scripts import.

What the backend and the console rely on:
  * agent.run(instruction) -> dict with 'started_at', 'duration_seconds',
    'actions', 'tokens', 'question'
  * entries of 'actions' keep the keys 'iteration', 'action', 'args', 'result';
    completion is a 'task_complete' entry with 'claim', 'verified', 'reason'
    (the backend takes the answer from it)
  * the debug screenshot is written to $TEMP/agent_screenshot_debug.png with
    every look at the screen
  * logging.getLogger("agent") is the channel the backend hooks
  * the log markers [SCREENSHOT_READY], [TASK_RESULT], [TODOS] (the full plan as
    one-line JSON) and [TODO] (single tick and add events) drive the console
"""

from __future__ import annotations

import logging

from harness.config import AgentConfig, _load_config
from harness.loop import ComputerAgent
from harness.paths import _stop_flag_path
from harness.storage import _harness_version, clear_session

__all__ = [
    "AgentConfig",
    "ComputerAgent",
    "_harness_version",
    "_load_config",
    "_stop_flag_path",
    "clear_session",
    "interactive",
    "main",
]

log = logging.getLogger("agent")


def interactive(agent: ComputerAgent) -> None:
    print("\n" + "=" * 72)
    print("COMPUTER AGENT")
    print("=" * 72)
    print("Type an instruction, 'new' to start a fresh conversation, or 'quit' to exit.\n")
    while True:
        try:
            instr = input("task > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not instr:
            continue
        if instr.lower() in {"quit", "exit", "q"}:
            break
        if instr.lower() in {"new", "reset"}:
            clear_session()
            print("Started a fresh conversation (context and last screenshot cleared).")
            continue
        try:
            agent.run(instr)
        except Exception:
            log.exception("Task failed")


def main() -> None:
    cfg = _load_config()
    log.info("Planner: %s (%s) | Decider: %s (%s)",
             cfg.planner.model, cfg.planner.endpoint,
             cfg.decider.model,
             "decision model" if cfg.decider.is_decision_model else "chat model")
    clear_session()  # startup is always a fresh conversation
    agent = ComputerAgent(cfg)
    interactive(agent)


if __name__ == "__main__":
    main()
