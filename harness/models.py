"""Calls to the planner and the decider."""

from __future__ import annotations

import base64
import json
import logging
import math
import threading
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
from PIL import Image

from .config import ModelProfile
from .hotkeys import _StopRequested
from .matching import _norm_text
from .perception import _BILINEAR
from .plan import Decision, Plan, Step, _parse_json_object, parse_plan
from .prompts import _AUTONOMOUS_NOTE, PLAN_SCHEMA, PLANNER_SYSTEM_PROMPT

log = logging.getLogger("agent")


# The model servers of a lab are reached over an internal address, often with a
# certificate of the lab's own: the warning about it would fill the log.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _BadRequest(RuntimeError):
    """HTTP 400: the server rejected the request body. Retrying it unchanged is pointless."""


class ModelCalls:
    """The part of ComputerAgent that asks the planner and the decider."""

    def _dump_context(self, role: str, text: str) -> None:
        """Write the text of the latest model request to a file, overwriting each
        call, so a run can be debugged from what the model actually saw."""
        try:
            self._CONTEXT_DUMP_PATH.write_text(f"[{role}]\n{text}\n", encoding="utf-8")
        except Exception:
            pass  # never let debug output break the agent

    def _send(self, profile: ModelProfile, headers: Dict[str, str],
              body: Dict[str, Any], timeout: Optional[int] = None) -> requests.Response:
        """POST in a worker thread, so that a stop does not have to wait for the
        model: the request is abandoned (its thread is a daemon) and the run
        ends at once."""
        box: Dict[str, Any] = {}

        def send() -> None:
            try:
                box["resp"] = requests.post(
                    profile.endpoint, headers=headers, json=body,
                    verify=self.cfg.verify_tls, timeout=timeout or self.cfg.request_timeout,
                )
            except Exception as exc:
                box["exc"] = exc

        worker = threading.Thread(target=send, daemon=True, name="agent-request")
        worker.start()
        while worker.is_alive():
            worker.join(0.2)
            if self._stop_source():
                raise _StopRequested()
        if "exc" in box:
            raise box["exc"]
        return box["resp"]

    def _post(self, profile: ModelProfile, body: Dict[str, Any], role: str) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[Exception] = None
        decider = role == "decider"
        retries = min(1, self.cfg.request_retries) if decider else self.cfg.request_retries
        for attempt in range(1 + retries):
            if self._stop_source():
                raise _StopRequested()
            t0 = time.monotonic()
            try:
                resp = self._send(profile, headers, body,
                                  self.cfg.decider_timeout if decider else None)
                if resp.status_code == 400:
                    raise _BadRequest(" ".join(resp.text.split())[:300])
                resp.raise_for_status()
                return resp.json()
            except _BadRequest:
                raise
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt
                # [RETRY] is timeline-visible: a timed-out long generation that
                # restarts from scratch is the #1 cause of silent multi-minute
                # stalls, and it should never look like a hang to the user.
                log.info("[RETRY] %s call failed after %.0fs (%s) — attempt %d/%d, retrying in %ds",
                         role, time.monotonic() - t0,
                         " ".join(str(exc).split())[:160],
                         attempt + 1, 1 + retries, wait)
                time.sleep(wait)
        raise RuntimeError(f"{role} call failed after {retries + 1} attempts: {last_exc}")

    def _count_tokens(self, usage: Dict[str, Any]) -> None:
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self._tokens["input"] += inp
        self._tokens["output"] += out
        self._tokens["total"] += inp + out
        self._tokens["calls"] += 1

    def _image_part(self, profile: ModelProfile) -> Dict[str, Any]:
        """The annotated screenshot as a chat image part, scaled down if the
        model accepts only smaller images."""
        b64 = self._shot_b64
        if profile.image_max_pixels:
            img = Image.open(BytesIO(base64.b64decode(b64)))
            if img.width * img.height > profile.image_max_pixels:
                scale = math.sqrt(profile.image_max_pixels / (img.width * img.height))
                img = img.resize((int(img.width * scale), int(img.height * scale)), _BILINEAR)
                buf = BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

    def _chat(self, profile: ModelProfile, body: Dict[str, Any], role: str) -> str:
        """POST a chat request and return the reply text. A server that rejects
        the JSON schema gets the same request without it: the prompt asks for
        JSON as well."""
        try:
            reply = self._post(profile, body, role)
        except _BadRequest as exc:
            if "response_format" not in body:
                raise
            log.info("[RETRY] %s request rejected (%s) — retrying without the JSON schema",
                     role, exc)
            reply = self._post(profile, {k: v for k, v in body.items() if k != "response_format"},
                               role)
        self._count_tokens(reply.get("usage") or {})
        choice = (reply.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            log.warning("%s returned no text (finish_reason=%s) — a reasoning model may have "
                        "spent its budget on thinking; switch thinking off via %s_REQUEST",
                        role, choice.get("finish_reason"), profile.name)
        return content

    def _plan(self, instruction: str, plan: Plan, note: str,
              escalate: bool = False) -> Optional[Dict[str, Any]]:
        """One planner round: the steps for the current screen, or None if the
        call failed or the reply was not a plan. A planner that can see also
        gets the annotated screenshot (PLANNER_IMAGES=failed: only after a
        failed step)."""
        parts = [f"USER REQUEST: {instruction}"]
        if self.cfg.autonomous:
            parts.append(_AUTONOMOUS_NOTE)
        if self._prior_tasks:
            parts.append("EARLIER REQUESTS IN THIS CONVERSATION (already handled): "
                         + "; ".join(self._prior_tasks))
        if self._history:
            parts.append("DONE EARLIER IN THIS CONVERSATION:\n" + "\n".join(self._history[-30:]))
        parts.append("DONE SO FAR FOR THIS REQUEST:\n" + (plan.render_done() or "  (nothing yet)"))
        # The planner starts every round from nothing. Without its own
        # thoughts it loses the thread: over a step that only moves the view
        # ("not saved yet, the button is further down" - scroll - "complete"),
        # and on a long request over what it found out rounds ago. The list
        # of steps says what was done, these say why.
        if self._notes:
            parts.append("YOUR ASSESSMENTS IN THE EARLIER ROUNDS OF THIS REQUEST, OLDEST "
                         "FIRST (the last one is from the round before this one):\n"
                         + self._own_notes())
        if self._unsaved:
            parts.append("ENTERED SINCE THE LAST SAVING STEP (no step with commits true has "
                         "followed): " + "; ".join(self._unsaved))
        if plan.open:
            parts.append("LEFT OPEN (failed and set aside; the user is told at the end):\n"
                         + plan.render_open())
        if note:
            parts.append("NOTE: " + note)
        if plan.pending():
            parts.append("STEPS STILL PLANNED, NOT CARRIED OUT YET (keep_planned true: they "
                         "follow the steps you return, as they stand; false: they are "
                         "dropped):\n" + "\n".join(
                             f"  {i}. {st.text}" for i, st in enumerate(plan.pending(), 1)))
        prof = self.cfg.planner
        if escalate:
            self._need_controls()   # an escalated round also lists the controls without text
            prof = self.cfg.escalation or self.cfg.planner
        # The list of texts does not say what lies where: a menu entry dimmed
        # behind a dialog stands in it next to the dialog's own list, and
        # reads as one of its options. The picture shows it.
        with_image = ((escalate or self.cfg.planner_images == "always")
                      and prof.vision and bool(self._shot_b64))
        if with_image:
            parts.append("The attached screenshot shows the current screen with the same "
                         "element ids drawn on it. It shows what the list cannot say: which "
                         "elements belong to a dialog or a list that is open and which lie "
                         "behind it, what is selected, what is greyed out.")
        # With the controls without text wherever they have been detected for
        # this screen (a step worked on a field, or this round is escalated).
        parts.append("CURRENT SCREEN:\n" + self._format_elements(self._elements))
        text = "\n\n".join(parts)
        self._dump_context("planner", text)
        body = {
            "model": prof.model,
            "messages": [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user",
                 "content": [self._image_part(prof), {"type": "text", "text": text}]
                 if with_image else text},
            ],
            "max_tokens": self.cfg.planner_max_tokens,
            "temperature": self.cfg.temperature,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "plan", "schema": PLAN_SCHEMA, "strict": True}},
            **prof.request,
        }
        if self._typing_next:
            # The controls, while the planner writes - but only where a field
            # is the likely first step. Started for a plan that begins with a
            # click to another view, the detection cannot be stopped and runs
            # into the reading of that view, which then takes three times as
            # long (10-14 s instead of 3 s, five times in one run).
            self._detect_ahead()
        t0 = time.monotonic()
        try:
            content = self._chat(prof, body, "planner")
        except Exception as exc:
            log.error("planner call failed: %s", exc)
            if self._runlog is not None:
                self._runlog.planner_round(prof.model, text, f"(the call failed: {exc})",
                                           self._prev_screenshot)
            return None
        finally:
            self._timing["planner"] += time.monotonic() - t0
            self._calls["planner"] += 1
        if self._runlog is not None:
            self._runlog.planner_round(prof.model, text, content, self._prev_screenshot)
        reply = parse_plan(content)
        if reply is None:
            log.warning("planner reply is not a JSON plan: %s", " ".join(content.split())[:200])
        else:
            self._last_assessment = reply["assessment"]
            if self._notes is None:
                self._notes = []
            self._notes.append(reply["assessment"])
        return reply

    def _confident(self, decision: Decision, min_conf: Optional[float] = None) -> bool:
        """Is the answer acted on? A probability below the threshold is not; an
        answer without probability (chat model) is taken as given. *min_conf*
        raises the threshold for a question whose answer is acted on without
        a second look."""
        need = max(self.cfg.decider_min_conf, min_conf or 0.0)
        return decision.choice is not None and (
            decision.confidence is None or decision.confidence >= need)

    # Failed decider calls in a row, and whether it is left out for the rest
    # of this request (reset in run()).
    _decider_fails = 0

    # A dialog the plan foresaw: the ids of what vanished when it opened (it
    # covered them), and the step that is to click one of the ids that came.
    _covered: frozenset = frozenset()

    _seen_texts: frozenset = frozenset()   # every text read on screen in this run

    _typing_next = False   # the decision model said: the next thing on this screen is typing

    # Per field label, how a value got into it earlier in this request:
    # 'typed' or 'list'. Only decides what is tried first.
    _field_way: Optional[Dict[str, str]] = None

    # The planner's assessments of this request, one per round, oldest first.
    _notes: Optional[List[str]] = None

    def _own_notes(self) -> str:
        """The planner's assessments of the earlier rounds, for its next
        round. One written several times in a row is listed once."""
        merged: List[List[Any]] = []
        for text in self._notes or []:
            if merged and merged[-1][0] == text:
                merged[-1][1] += 1
            else:
                merged.append([text, 1])
        return "\n".join(
            f"  {i}. {text}" + (f" (written {n} times in a row)" if n > 1 else "")
            for i, (text, n) in enumerate(merged, 1))

    def _way(self, step: Step) -> str:
        return (self._field_way or {}).get(_norm_text(step.target), "")

    def _note_way(self, step: Step, way: str) -> None:
        if self._field_way is None:
            self._field_way = {}
        self._field_way[_norm_text(step.target)] = way

    _foreseen: Tuple[Optional[int], frozenset] = (None, frozenset())

    _decider_down = False

    def _decide(self, state: str, key: str, instructions: str, options: List[str],
                with_image: bool = False, labels: Optional[Dict[str, str]] = None,
                describe: Optional[Dict[str, str]] = None,
                min_conf: Optional[float] = None) -> Decision:
        """One typed choice question to the decider. A decision model returns a
        probability per option; a chat model is held to the options by a JSON
        schema and returns none. An answer outside the options is no answer.
        *labels* is the readable form of each option for the UI; *describe*
        (else the labels) tells a decision model what each option stands for."""
        if self._decider_down:
            return Decision()   # no answer: code or the planner decides
        prof = self.cfg.decider
        self._dump_context("decider", f"{state}\n\nQUESTION [{key}]: {instructions}\n"
                                      f"OPTIONS: {', '.join(options)}")
        choice: Optional[str] = None
        confidence: Optional[float] = None
        t0 = time.monotonic()
        try:
            if prof.is_decision_model:
                body: Dict[str, Any] = {
                    "model": prof.model,
                    "state": state,
                    # criteria maps every option to a description, or null
                    # where the option speaks for itself (yes / no).
                    "questions": {key: {"type": "choice", "instructions": instructions,
                                        "criteria": {o: (describe or labels or {}).get(o)
                                                     for o in options}}},
                    **prof.request,
                }
                if with_image:
                    body["images"] = [self._image_part(prof)["image_url"]["url"]]
                reply = self._post(prof, body, "decider")
                self._count_tokens(reply.get("usage") or {})
                answer = (reply.get("answers") or {}).get(key) or {}
                choice = answer.get("choice")
                prob = (answer.get("probabilities") or {}).get(choice)
                confidence = float(prob) if prob is not None else None
            else:
                text = (
                    f"{state}\n\nQUESTION: {instructions}\n"
                    f"ALLOWED ANSWERS: {', '.join(options)}\n\n"
                    f'Reply with a JSON object only: {{"{key}": "<one allowed answer>"}}'
                )
                body = {
                    "model": prof.model,
                    "messages": [{"role": "user",
                                  "content": [self._image_part(prof),
                                              {"type": "text", "text": text}]
                                  if with_image else text}],
                    "max_tokens": 64,
                    "temperature": 0.0,
                    "response_format": {"type": "json_schema", "json_schema": {
                        "name": "decision", "strict": True,
                        "schema": {"type": "object",
                                   "properties": {key: {"type": "string", "enum": options}},
                                   "required": [key], "additionalProperties": False}}},
                    **prof.request,
                }
                obj = _parse_json_object(self._chat(prof, body, "decider")) or {}
                choice = obj.get(key)
            self._decider_fails = 0
        except Exception as exc:
            log.error("decider call failed: %s", exc)
            self._decider_fails += 1
            if self._decider_fails >= 2:
                self._decider_down = True
                log.warning("[ISSUE] the decider does not answer - the planner decides alone "
                            "for the rest of this request")
        finally:
            self._timing["decider"] += time.monotonic() - t0
            self._calls["decider"] += 1
        if choice not in options:
            choice = None
        log.info("[THOUGHT] decider · %s → %s%s", key, choice if choice is not None else "no answer",
                 f" (p={confidence:.2f})" if confidence is not None else "")
        decision = Decision(choice, confidence)
        # The same answer, machine-readable, for the UI: tied to the step, with
        # the option's readable form and whether it is acted on.
        log.info("[DECISION] %s", json.dumps(
            {"question": key, "step": self._step_id, "choice": choice,
             "label": (labels or {}).get(choice, choice), "p": confidence,
             "accepted": self._confident(decision, min_conf), "options": len(options),
             "seconds": round(time.monotonic() - t0, 2), "image": with_image},
            ensure_ascii=False, separators=(",", ":")))
        return decision

    def _ask(self, state: str, key: str, instructions: str, options: List[str],
             look: bool = False, labels: Optional[Dict[str, str]] = None,
             describe: Optional[Dict[str, str]] = None,
             min_conf: Optional[float] = None) -> Optional[str]:
        """The option the decider is confident in, or None when it is not — the
        caller then falls back to what code knows, or to the planner. With
        `look`, the question is about something only the screenshot shows."""
        mode = self.cfg.decider_images
        can_see = self.cfg.decider.vision and bool(self._shot_b64) and mode != "never"
        if look and not can_see:
            return None
        first_with_image = can_see and (look or mode == "always")
        decision = self._decide(state, key, instructions, options, with_image=first_with_image,
                                labels=labels, describe=describe, min_conf=min_conf)
        if not self._confident(decision, min_conf) and can_see and not first_with_image:
            decision = self._decide(state, key, instructions, options, with_image=True,
                                    labels=labels, describe=describe, min_conf=min_conf)
        if self._confident(decision, min_conf):
            self._decider_used = True
            return decision.choice
        return None

    def _state(self, step: Optional[Step] = None, extra: str = "") -> str:
        """The decider's context. Request and screen come first: they stay the
        same across the questions asked on one screen, so a server with prefix
        caching reads them once."""
        parts = [f"USER REQUEST: {self._instruction}",
                 "SCREEN:\n" + self._format_elements(self._elements, limit=150,
                                                     include_yolo=False)]
        if step is not None:
            parts.append(f"CURRENT STEP: {step.text}")
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)
