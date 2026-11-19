"""
The harness of the computer use agent: it works in the window in front, from
screenshots only. Text is read with RapidOCR, controls without text are found
with a YOLO detector; no structural UI tree is used, so it also works on
remote applications that offer none.

Three layers, slowest first:
  PLANNER  a chat model. Called rarely: it writes the steps for a screen, and
           is asked again only when the work cannot go on (a step failed, the
           screen changed in an unplanned way, the work may be complete).
  DECIDER  a decision model on /v1/systemone, or a chat model held to the same
           options. It answers short choice questions: which element a step
           means, whether a click had its effect, whether the plan still fits
           the screen, whether the request is complete.
  CODE     perception, input, typing the planned values and the checks on
           screen. Where an exact label match leaves one candidate, no model
           is asked at all.

The modules, from the bottom up:
  paths       where files lie
  config      the settings of a run, read from the environment
  prompts     what the models are told
  elements    the elements of a screen and the registry of their ids
  perception  OCR and the YOLO detector
  screen      the window in front: its picture, comparisons, the annotation
  hotkeys     the global stop and pause keys
  actions     mouse and keyboard
  plan        the plan and its steps
  matching    checks on the text of a screen that need no model
  candidates  the elements a step can mean
  storage     the session file and the run log
  reading, models, acting, steps
              the parts of the agent: reading the screen, model calls, acting
              and observing, carrying out one step
  loop        ComputerAgent and the run loop of one request
"""

import logging
import os

from dotenv import load_dotenv

# Before any module of the package is imported: several of them read their
# settings from the environment when they are imported.
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
