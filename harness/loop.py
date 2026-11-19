"""The agent: the run loop of one request."""

from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui
from PIL import Image

from .acting import Acting
from .actions import ActionExecutor, _enable_dpi_awareness
from .candidates import Candidate
from .config import AgentConfig
from .elements import ChangeSet, Element, ElementRegistry
from .hotkeys import _Hotkeys, _StopRequested, _TakenOver
from .matching import (
    _norm_text,
    _value_in_text,
    check_evidence_in_ocr,
    format_evidence_rejection,
    log_evidence_summary,
)
from .models import ModelCalls
from .paths import _pause_flag_path, _stop_flag_path
from .perception import (
    _get_rapidocr_crop_engine,
    _get_rapidocr_engine,
    _get_yolo_session,
)
from .plan import Plan, Step
from .prompts import (
    _MAX_CHOICES,
    _NONE,
    _TOGGLED,
    _UNSAVED_NOTE,
    Q_COMMITS,
    Q_COMPLETE,
    Q_COMPLETE_REST,
    Q_LOOP,
    Q_NEXT,
    Q_NEXT_GROUP,
    Q_SCREEN,
    Q_WINDOW,
)
from .reading import ScreenReading
from .screen import _foreground_window
from .steps import Steps
from .storage import RunLog, _harness_version, _run_outcome, load_session, save_session

log = logging.getLogger("agent")


class ComputerAgent(ScreenReading, ModelCalls, Acting, Steps):
    """The agent. One instance carries out the requests of a conversation, one
    after the other; run() is the entry. Its methods are spread over the parts it
    is built from: reading the screen, model calls, acting, and the steps."""

    def __init__(self, config: AgentConfig):
        self.cfg = config
        _enable_dpi_awareness()

        if platform.system() == "Windows":
            import ctypes
            user32 = ctypes.windll.user32
            self.width, self.height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        else:
            self.width, self.height = pyautogui.size()
        log.info("Primary monitor: %dx%d", self.width, self.height)

        # Pre-warm both engines at startup (single-threaded) so the first
        # _parse_screen call pays zero init cost and parallel inference is safe.
        log.info("Initializing OCR engine ...")
        _get_rapidocr_engine()
        _get_rapidocr_crop_engine()
        log.info("Initializing YOLO icon detector ...")
        _get_yolo_session()

        self.registry = ElementRegistry()
        self.executor = ActionExecutor(self.width, self.height, self.registry)
        self._prev_screenshot: Optional[Image.Image] = None  # raw frame, for the pixel diffs
        # Fast paths: cached result of the last parse (reused whole on an
        # unchanged frame, in part on a locally changed one).
        self._last_elements: Optional[List[Element]] = None
        self._last_rect: Optional[Tuple[int, int, int, int]] = None
        self._parses = {"full": 0, "partial": 0, "cached": 0, "controls": 0}
        # Has the control detector run on the cached frame? It costs several
        # seconds on a CPU, three times the text reading, and only steps that
        # work on fields need it — so a full parse reads text only, and
        # _need_controls() adds the controls when a step asks for them.
        self._controls_ready = False
        # The current observation and the run's bookkeeping (reset by run()).
        self._instruction = ""
        self._elements: List[Element] = []
        self._shot_b64 = ""
        self._last_change = ChangeSet()
        self._actions: List[Dict[str, Any]] = []
        self._iteration = 0
        self._tokens = {"input": 0, "output": 0, "total": 0, "calls": 0}
        self._timing = {"perception": 0.0, "planner": 0.0, "decider": 0.0, "input": 0.0}
        self._calls = {"planner": 0, "decider": 0}
        self._prior_tasks: List[str] = []
        self._history: List[str] = []
        self._escape_unsafe = False               # Escape once removed a typed value
        # Batched entry: fields typed into and not read back yet; those whose
        # read-back failed; those that could not be put right (for the run
        # loop).
        self._batch: List[Tuple[Step, Candidate]] = []
        self._batch_failed: List[Tuple[Step, Candidate, str, str]] = []
        self._late: List[Tuple[Step, str]] = []
        self._target_pid: Optional[int] = None    # process of the application's window
        self._last_assessment = ""                # the planner's own words in its last round
        self._notes = []
        self._unsaved: List[str] = []             # entries no committing step has followed
        self._step_id: Optional[int] = None       # the step now running, for the UI
        self._decider_used = False                # an accepted decider answer in this step
        self._pointer: Optional[Tuple[int, int]] = None   # where the agent left the pointer
        self._hotkeys = _Hotkeys(os.getenv("STOP_KEY", "pause"),
                                 os.getenv("PAUSE_KEY", "scrolllock"))
        self._hotkeys.start()
        self._hotkeys.ready.wait(2.0)
        if self._hotkeys.active:
            log.info("Operator keys: stop = %s | pause/resume = %s",
                     self._hotkeys.active.get("stop", "(none)"),
                     self._hotkeys.active.get("pause", "(none)"))

    def _navigate(self, plan: Plan) -> Optional[Element]:
        """Let a decision model choose the next navigation click: the element
        to click, or None when the planner has to decide (typing is due, the
        request looks complete, the choice is not confident, or the click would
        commit something). A decision model takes a limited number of options,
        so the choice runs in two rounds: the best of each group of elements,
        then the winners against 'type' and 'done'."""
        texts = sorted((el for el in self._elements
                        if el.source != "yolo" and (el.text or "").strip()),
                       key=lambda el: (el.center_y // 16, el.center_x))
        if not texts:
            return None
        by_id = {el.stable_id: el for el in texts}
        describe = {el.stable_id: f"'{el.text.strip()[:50]}' at ({el.center_x},{el.center_y})"
                    for el in texts}
        labels = {el.stable_id: el.text.strip() for el in texts}
        state = self._state(None, "STEPS CARRIED OUT SO FAR:\n"
                                  + (plan.render_done() or "  (none)"))
        winners: List[str] = []
        ids = list(by_id)
        for i in range(0, len(ids), _MAX_CHOICES):
            group = ids[i:i + _MAX_CHOICES]
            decision = self._decide(state, "next", Q_NEXT_GROUP, group + [_NONE], labels=labels,
                                    describe={**{k: describe[k] for k in group},
                                              _NONE: "none of these elements"})
            if decision.choice in by_id:
                winners.append(decision.choice)
        if not winners:
            return None
        # The last word is given with the screenshot where the decider can
        # see: whether a tab or a form is already open is not in the text.
        look = (self.cfg.decider.vision and bool(self._shot_b64)
                and self.cfg.decider_images != "never")
        decision = self._decide(
            state, "next", Q_NEXT, winners + ["type", "done"], with_image=look, labels=labels,
            min_conf=self.cfg.decider_nav_conf,
            describe={**{k: describe[k] for k in winners},
                      "type": "no click: a visible field has to be filled or a value chosen",
                      "done": "nothing to do: the screen already shows what was asked for"})
        self._typing_next = decision.choice == "type"
        if (decision.choice not in by_id or decision.confidence is None
                or decision.confidence < self.cfg.decider_nav_conf):
            return None
        target = by_id[decision.choice]
        commits = self._decide(
            self._state(None, f"ELEMENT: {describe[target.stable_id]}"), "commits",
            Q_COMMITS, ["yes", "no"])
        if commits.choice != "no" or not self._confident(commits, self.cfg.decider_nav_conf):
            return None   # a click that commits something is the planner's to plan
        self._decider_used = True
        return target

    def _plan_still_fits(self, step: Step, plan: Plan, change: ChangeSet,
                         before: Optional[List[Element]] = None) -> Optional[str]:
        """After a successful step: can the remaining steps go on? Returns None,
        or the reason the planner has to look at the screen again. Asked only
        when the screen did something the step does not explain."""
        covered, self._covered = self._covered, frozenset()
        if not plan.pending():
            return None
        if change.transition == "replaced":
            return "the screen changed to a different view"
        came = [c for c in change.appeared
                if c.element.source != "yolo" and (c.element.text or "").strip()]
        nxt = plan.pending()[0]
        want = _norm_text(nxt.target)

        def showing(elements: List[Element]) -> List[str]:
            return [el.stable_id for el in elements
                    if el.source != "yolo" and _norm_text(el.text) == want]
        hit = [c.element.stable_id for c in came if _norm_text(c.element.text) == want]
        if not hit and before is not None and not showing(before):
            # Asked of the texts, not only of the ids: a dialog's 'Delete'
            # next to a list's '× Delete' buttons is taken by the registry for
            # one of those that moved, and is not reported as new.
            hit = showing(self._elements)
        if nxt.op == "click" and want and not nxt.row and hit:
            # The next step clicks something that came with this one: the plan
            # foresaw what appeared (a dialog asking to confirm). What vanished
            # at the same moment is what the dialog covers; when it comes back
            # as the dialog closes, that is not unforeseen either.
            log.info("what appeared holds the target of the next step ('%s'): foreseen",
                     nxt.target)
            self._covered = frozenset(c.element.stable_id for c in change.disappeared)
            self._foreseen = (nxt.id, frozenset(hit))
            return None
        unforeseen = [
            c for c in came
            if not (step.value and _value_in_text(step.value, c.element.text))
            and c.element.stable_id not in covered
        ]
        if change.transition != "popup" and not unforeseen:
            return None
        appeared = ", ".join(c.as_line() for c in unforeseen[:12]) or change.summary()
        remaining = "\n".join(f"  {s.text}" for s in plan.pending())
        state = self._state(None, f"LAST STEP (succeeded): {step.text}\n"
                                  f"APPEARED ON SCREEN AFTER IT: {appeared}\n"
                                  f"REMAINING STEPS:\n{remaining}")
        # Before a check the answer has to be as sure as for a navigation
        # click: under a dialog that dims the window half of a list is not
        # read, and "gone" would pass for an entry that is still there.
        if self._ask(state, "screen", Q_SCREEN, ["continue", "replan"],
                     min_conf=self.cfg.decider_nav_conf if nxt.op == "check" else None
                     ) == "continue":
            return None
        return f"after the step \"{step.text}\" this appeared on screen: {appeared}"

    def _window_titles(self) -> List[str]:
        listing = ActionExecutor._list_windows()
        return [line[4:] for line in listing.splitlines() if line.startswith("  - ")]

    def _focus_target(self, request: str, hints: List[Optional[str]]
                      ) -> Optional[Tuple[str, List[str]]]:
        """Bring the application the request is about to the foreground. *hints*
        are window-title substrings to try first (the window of this
        conversation, TARGET_WINDOW, the user's reply to this very question).
        Returns None on success, else (question, options) for the user."""
        for title in hints:
            if title:
                res = self.executor._focus_window(title)
                log.info("[FOCUS] %r → %s", title, res)
                if self.executor.focused_once:
                    return None
        titles = self._window_titles()[:_MAX_CHOICES]
        if titles:
            keys = [f"w{i + 1}" for i in range(len(titles))]
            state = (f"USER REQUEST: {request}\n\nOPEN WINDOWS:\n"
                     + "\n".join(f"  {k}: {t}" for k, t in zip(keys, titles)))
            choice = self._ask(state, "window", Q_WINDOW, keys + [_NONE],
                               labels=dict(zip(keys, titles)))
            if choice in keys:
                title = titles[keys.index(choice)]
                res = self.executor._focus_window(title)
                log.info("[FOCUS] %r → %s", title, res)
                if self.executor.focused_once:
                    return None
        return ("Which window is the application for this request? (It has to be open. "
                "TARGET_WINDOW in .env fixes it for good.)", titles[:12])

    def _finish(self, claim: str, reason: str, quoted: Optional[List[str]] = None,
                open_points: Optional[List[str]] = None) -> None:
        log.info("[TASK_RESULT] %s", claim)
        # What the result rests on, for the UI: the on-screen texts as a JSON
        # list, or a sentence when it rests on the checked steps.
        log.info("[EVIDENCE] %s",
                 json.dumps(quoted, ensure_ascii=False) if quoted else reason)
        self._actions.append({"iteration": self._iteration, "action": "task_complete",
                              "claim": claim, "verified": True, "reason": reason,
                              "open": list(open_points or [])})

    _runlog: Optional[RunLog] = None   # the log of the request being carried out

    def run(self, instruction: str) -> Dict[str, Any]:
        """Carry out one request, and keep its log on disk (RunLog)."""
        record = self._runlog = RunLog(self.cfg.run_log_dir, self.cfg.run_log)
        if record.dir is not None:
            log.info("Run log: %s", record.dir)
            log.info("Harness %s | planner: %s | decider: %s | after a failed step: %s | %s",
                     _harness_version() or "(no git)", self.cfg.planner.model,
                     self.cfg.decider.model,
                     self.cfg.escalation.model if self.cfg.escalation else "the planner",
                     "unattended" if self.cfg.autonomous else "attended")
        outcome, said = "error", ""
        try:
            result = self._run(instruction)
            outcome, said = _run_outcome(result)
            return result
        except BaseException as exc:
            said = f"{type(exc).__name__}: {exc}"
            if record.dir is not None:
                log.exception("the run ended on an error")
            raise
        finally:
            record.close(instruction, outcome, said, getattr(self, "_iteration", 0),
                         getattr(self, "_calls", {}))
            self._runlog = None

    def _run(self, instruction: str) -> Dict[str, Any]:
        start = time.time()
        start_dt = datetime.datetime.now()
        log.info("=" * 70)
        log.info("TASK: %s", instruction)
        log.info("=" * 70)

        # Reset per-run state (registry/executor are per-process anyway).
        self._prev_screenshot = None
        self._last_elements = None
        self._last_rect = None
        self.registry = ElementRegistry()
        self.executor = ActionExecutor(self.width, self.height, self.registry)
        self._elements = []
        self._shot_b64 = ""
        self._last_change = ChangeSet()
        self._actions = []
        self._iteration = 0
        self._tokens = {"input": 0, "output": 0, "total": 0, "calls": 0}
        self._timing = {"perception": 0.0, "planner": 0.0, "decider": 0.0, "input": 0.0}
        self._calls = {"planner": 0, "decider": 0}
        self._parses = {"full": 0, "partial": 0, "cached": 0, "controls": 0}
        self._controls_ready = False
        self._escape_unsafe = False
        self._last_assessment = ""
        self._notes = []
        self._unsaved = []
        self._target_pid = None
        self._decider_fails, self._decider_down = 0, False
        self._covered, self._foreseen = frozenset(), (None, frozenset())
        self._seen_texts = frozenset()
        self._field_way = {}
        self._batch, self._batch_failed, self._late, self._last_target = [], [], [], ""
        self._step_id = None
        self._pointer = None
        self._hotkeys.stop_requested.clear()
        for flag in (_pause_flag_path(), _stop_flag_path()):   # left over from an earlier run
            try:
                flag.unlink()
            except OSError:
                pass

        # Load prior conversation context for continuity (empty on a fresh
        # conversation — startup and "New conversation" clear the session file).
        session = load_session() if self.cfg.continue_session else {}
        self._prior_tasks = list(session.get("tasks", []))
        self._history = list(session.get("history", []))
        if self._prior_tasks:
            log.info("Continuing conversation: %d earlier request(s).", len(self._prior_tasks))
        # If the last run ended on a question, this message is the user's answer
        # to it, not a new request: the request stays the one that was asked
        # about, with question and answer attached.
        open_question = session.get("open_question") or {}
        request = instruction
        if open_question:
            request = (f"{open_question.get('request', '')}\n\n"
                       f"You asked the user: \"{open_question.get('question', '')}\"\n"
                       f"The user answered: \"{instruction}\"")
            log.info("This message answers the question the last run ended on.")
        self._instruction = request

        plan = Plan()
        # Set aside before the question this message answers: still to be reported.
        plan.open = [str(p) for p in open_question.get("open") or []]
        log.info("[TODOS] %s", plan.to_marker())
        if plan.open:
            log.info("[OPEN] %s", json.dumps(plan.open, ensure_ascii=False))
        # The keys that are really registered (null: disabled, unknown, or taken).
        log.info("[KEYS] %s", json.dumps({"stop": self._hotkeys.active.get("stop"),
                                          "pause": self._hotkeys.active.get("pause")}))

        due = False            # the planner is to be asked although steps are still planned
        stood = False          # the plan it is asked about was final: complete once carried out

        def replan(reason: str, detail: str, after: Optional[Step], drop: bool = True) -> None:
            """The planner is asked again, said in a form the UI can show as a
            break in the plan. After a pause the pending steps are retired at
            once (*drop*): the user may have worked on the screen. After a
            failed step or an unforeseen change they stay: they name their
            targets by label and are checked when they run, and the planner,
            who sees them, says whether they still stand."""
            nonlocal due, stood
            dropped = plan.drop_pending(f"replanned: {detail}") if drop else []
            due = not drop
            stood = not drop and (stood or final)
            log.info("[REPLAN] %s", json.dumps(
                {"reason": reason, "step": after.id if after else None,
                 "detail": detail, "dropped": dropped},
                ensure_ascii=False, separators=(",", ":")))

        pending_question: Optional[str] = None
        question_options: List[str] = []
        question_kind = ""

        def ask_user(text: str, options: List[str], kind: str = "") -> None:
            nonlocal pending_question, question_options, question_kind
            pending_question, question_options, question_kind = text, options, kind
            if self.cfg.autonomous:
                # Nobody to ask: the run ends here (see the end of the loop).
                log.info("[INTERVENE] nobody attends the run (AUTONOMOUS): it ends on what "
                         "it would have asked: %s", text)
                return
            log.info("[INTERVENE] ask_user: %s", text)
            # Plain text, or JSON when the question is a choice between known answers.
            log.info("[QUESTION] %s", json.dumps({"text": text, "options": options},
                                                 ensure_ascii=False) if options else text)

        note = "NEW USER REQUEST — plan the steps for the current screen."
        escalate = False       # the next planner round also gets the screenshot
        final = False          # the planner said: complete once these steps succeeded
        answer = ""
        rounds = 0
        planner_misses = 0     # planner rounds in a row without a usable reply
        failed_in_a_row = 0
        step: Optional[Step] = None
        # May the decider choose the next click? Not right after it handed over
        # to the planner, and not while a failed step is being sorted out.
        navigate = (self.cfg.decider_navigates and self.cfg.decider.is_decision_model)
        nav_turn = True
        last_nav = ""          # the text of the navigator's last click
        # Has data entry begun on the current view? From then on the planner
        # keeps the lead until the view is replaced: a click chosen inside a
        # form is no longer navigation but an entry (a day in a calendar, an
        # option of a list), and those are the planner's to decide.
        entering = False
        streak: List[Step] = []   # the steps that failed since the last one that worked
        unproven = 0              # completions in a row whose evidence was not on screen
        save_asked = False        # completion was held back once for unsaved entries
        rejected = 0              # plans in a row rejected for repeating a failed step
        forced = False            # a step was set aside by code since the last one that worked
        handed_back = False       # an unattended run: the planner's last reply was a question

        def signature(op: str, target: str, value: str, element: str = "", row: str = ""
                      ) -> Tuple[str, str, str, str, str]:
            # Element and row belong to it: the same button text with another
            # id, or in another row, is another button.
            return (op, _norm_text(target), _norm_text(value), element.strip().strip("[]"),
                    _norm_text(row))

        def tried() -> str:
            return "; ".join(f"{f.text} — {f.note}" for f in streak)

        def leave_open(why: str) -> None:
            """What failed at this point is set aside: the work goes on
            without it, and the user is told at the end. Named by the entry
            it was (the first try at each field), not by every way tried."""
            nonlocal failed_in_a_row
            seen, what = set(), []
            for f in streak:
                if f.op in ("fill", "select") and _norm_text(f.target) not in seen:
                    seen.add(_norm_text(f.target))
                    what.append(f.text)
            plan.open.append(f"{'; '.join(what) or streak[0].text} — {' '.join(why.split())}")
            log.info("[INTERVENE] set aside, the work goes on without it: %s", plan.open[-1])
            log.info("[OPEN] %s", json.dumps(plan.open, ensure_ascii=False))
            streak.clear()
            failed_in_a_row = 0

        def result(text: str) -> str:
            """The answer to the user: never without what was left open."""
            if not plan.open:
                return text or "The request was carried out."
            return ((text or "The rest of the request was carried out.")
                    + " NOT DONE: " + "; ".join(plan.open))

        def look_further() -> str:
            """Move the view one page down for a planner that keeps working on
            the visible part, and say so: the control it is looking for (a
            save or submit button) is usually below it."""
            if self._scroll_page(True).pixels_changed:
                return ("The view has been moved one page down for you: the control you "
                        "need may be among what is on screen now.")
            return "The page does not go on below what you see."

        def late_failures() -> None:
            """Entries of a batch that turned out not to have arrived and could
            not be put right: their steps fail after the fact, and the planner
            looks at the screen."""
            nonlocal note, escalate, final
            late, self._late = self._late, []
            if not late:
                return
            for st, why in late:
                st.status, st.note = "failed", why
                log.info("[TODO] ✗ %s", st.text)
                log.info("[ISSUE] %s", why)
                if st.text in self._unsaved:
                    self._unsaved.remove(st.text)
                streak.append(st)
            replan("failed_step", late[-1][1], late[-1][0], drop=False)
            note = ((note + " " if note else "") + "Entries typed earlier did not arrive: "
                    + "; ".join(f"{st.text} - {why}" for st, why in late)
                    + ". Read the screen and put them right.")
            escalate, final = True, False
            log.info("[TODOS] %s", plan.to_marker())

        extensions = 0                       # allowances granted beyond the limits
        step_budget = self.cfg.max_steps

        def may_go_on(what: str) -> bool:
            """A limit is reached. A long request that is still getting on is
            not a loop: the decision model says which it is, from the steps
            carried out. A confident 'progress' grants the allowance once
            more, three times at most; anything else stops as before."""
            nonlocal extensions
            if extensions >= 3:
                return False
            done = plan.render_done().splitlines()
            if len(done) > 60:              # a decision model's context is limited
                done = ["  (earlier steps left out)"] + done[-60:]
            state = (f"USER REQUEST: {request}\n\nPLANNING ROUNDS USED: "
                     f"{self._calls['planner']}\n\nSTEPS CARRIED OUT SO FAR:\n"
                     + ("\n".join(done) or "  (none)"))
            if self._ask(state, "loop", Q_LOOP, ["progress", "circles"],
                         min_conf=self.cfg.decider_nav_conf) != "progress":
                return False
            extensions += 1
            log.info("[INTERVENE] the limit of %s is reached, but the request is getting on — "
                     "going on (allowance %d of 3)", what, extensions)
            return True

        def stuck() -> None:
            """The planner has no further idea: a person who knows the
            application does. Ask, with what was tried."""
            ask_user(f"I am stuck at this step: {streak[-1].text}. What I tried: {tried()}. "
                     "How is this done here? You can also do the step yourself and then "
                     "tell me to continue.", [])

        def set_aside_or_ask() -> bool:
            """No further way for what failed at this point. It is set aside
            and the work goes on (True) - unless it saves or sends something
            (the request stands or falls with that), or nothing has worked
            since the last step set aside like this: then the user is asked
            (False)."""
            nonlocal forced, note
            if forced or any(f.commits for f in streak):
                stuck()
                return False
            forced = True
            first, n = streak[0].text, len(streak)
            leave_open(f"{n} {'way' if n == 1 else 'ways'} failed, the last one: {streak[-1].note}")
            note = (f"\"{first}\" could not be carried out and has been set aside. The user "
                    "is told about it at the end, with the reason: do not try it again and do "
                    "not ask the user about it. Carry on with the rest of the request as if it "
                    "had not been part of it, including saving. Ask the user only if no further "
                    "step of the request can be carried out without it.")
            return True

        try:
            asked = self._focus_target(request, [
                session.get("target_window"), self.cfg.target_window,
                instruction if open_question.get("kind") == "window" else None,
            ])
            if asked:
                ask_user(asked[0], asked[1], kind="window")
            else:
                front = _foreground_window()
                self._target_pid = front[0] if front else None
                # Decaret: a field may still hold a blinking caret from the previous
                # request, which OCR reads as an extra glyph.
                self._observe(decaret=True)

            while not pending_question:
                try:
                    self._checkpoint()
                    step = plan.next_pending()
                    if step is None or due:
                        # Nothing left to execute, or a step failed or the screen did
                        # something unforeseen: the work is complete, or the planner is due.
                        # Either way the fields typed into are read back first.
                        if self._flush_batch():
                            log.info("[TODOS] %s", plan.to_marker())
                        if final and self._batch:
                            self._give_up_unread()
                        late_failures()
                        if final and self._unsaved and not save_asked:
                            # Entered, not saved, declared complete: in most
                            # applications that loses the entries. Ask once.
                            save_asked, final, note = True, False, _UNSAVED_NOTE
                            log.info("[INTERVENE] completion held back — values were entered "
                                     "and no step saved them")
                        if final:
                            state = self._state(None, "STEPS CARRIED OUT, EACH CHECKED ON SCREEN:\n"
                                                      + plan.render_done()
                                                      + ("\n\nLEFT OPEN, THE USER IS TOLD:\n"
                                                         + plan.render_open() if plan.open else ""))
                            if self._ask(state, "complete",
                                         Q_COMPLETE_REST if plan.open else Q_COMPLETE,
                                         ["yes", "no"],
                                         min_conf=self.cfg.decider_nav_conf) == "yes":
                                self._finish(result(answer),
                                             "every step carried out was checked on screen and "
                                             "the result was confirmed", open_points=plan.open)
                                break
                            final, escalate = False, True   # the planner looks at the picture
                            note = ("The steps are carried out, but the completion check did NOT "
                                    "confirm that the request is complete. Look at the screen: "
                                    "plan what is missing, or return no steps with final true "
                                    "and the on-screen evidence.")
                        self._typing_next = False
                        if navigate and nav_turn and not escalate and not entering and not due:
                            target = self._navigate(plan)
                            if target is not None and target.text.strip() == last_nav:
                                # The same click again: the first did not get
                                # the request any further. The planner's turn.
                                log.info("[THOUGHT] decider · the same click again ('%s') — "
                                         "the planner decides", last_nav)
                                target = None
                            if target is not None:
                                last_nav = target.text.strip()
                                added = plan.add("click", target.text.strip(), "",
                                                 target.stable_id)
                                added.planned_by = "decider"
                                log.info("[TODO] + %s", added.text)
                                log.info("[TODOS] %s", plan.to_marker())
                                continue
                            nav_turn = False   # typing, done or unsure: the planner's turn
                        rounds += 1
                        if rounds > self.cfg.max_plan_rounds and may_go_on("planning rounds"):
                            rounds = 1
                        if rounds > self.cfg.max_plan_rounds:
                            # Out of planning rounds without a failure streak:
                            # the plan is going in circles. A person can say how.
                            last = [st for st in plan.steps() if st.status in ("done", "failed")][-4:]
                            ask_user(
                                f"I have used all {self.cfg.max_plan_rounds} planning rounds "
                                "without finishing. My last steps: "
                                + "; ".join(f"{st.text} — {st.note}" for st in last)
                                + ". How should I go on? You can also finish this part yourself "
                                  "and then tell me to continue.", [])
                            break
                        log.info("[SUPERVISE] %s", " ".join(note.split()) or "plan the next steps")
                        reply = self._plan(request, plan, note, escalate)
                        if reply is None:
                            planner_misses += 1
                            if planner_misses >= 3:
                                log.error("Planner produced no usable plan — aborting.")
                                self._actions.append({"iteration": self._iteration,
                                                      "error": "aborted: planner produced no plan"})
                                break
                            continue
                        planner_misses = 0
                        log.info("[NAV] on_track | %s", reply["assessment"])
                        if (reply["question"] and reply["final"] and not reply["steps"]
                                and reply["evidence"]
                                and check_evidence_in_ocr(reply["evidence"], self._elements)[0]):
                            # Complete, with the proof on screen, and a question in the
                            # same reply: the planner has judged for itself, and what it
                            # asks is whether the user agrees. Asked, it comes back with
                            # the answer and asks again.
                            log.info("[INTERVENE] the request is declared complete with its "
                                     "evidence on screen — the question in the same reply is "
                                     "not asked: %s", reply["question"])
                            reply["question"] = ""
                        if reply["question"] and self.cfg.autonomous and not handed_back:
                            # Once: the planner is to decide what it wanted to ask.
                            handed_back = True
                            log.info("[INTERVENE] nobody to ask — the planner decides itself: %s",
                                     reply["question"])
                            note = (f"You asked: \"{reply['question']}\" Nobody can answer: this "
                                    "request runs unattended. Decide it yourself, take the most "
                                    "plausible answer and go on. If the request cannot be "
                                    "carried out without the answer, return the question again: "
                                    "the run then ends with it as the reason.")
                            continue
                        if reply["question"]:
                            ask_user(reply["question"], reply["options"])
                            break
                        handed_back = False
                        if reply["steps"]:
                            first = reply["steps"][0]
                            if any(signature(first["op"], first["target"], first["value"],
                                             first["element"], first["row"])
                                   == signature(f.op, f.target, f.value, f.element, f.row)
                                   for f in streak):
                                # The same step again cannot end differently.
                                rejected += 1
                                log.info("[INTERVENE] plan rejected — it starts with a step "
                                         "that already failed here")
                                if rejected >= 2:
                                    # No other idea for it: the work goes on without.
                                    if not set_aside_or_ask():
                                        break
                                    rejected = 0
                                    continue
                                note = ("REJECTED: your plan starts with a step that already "
                                        f"failed at this point. Tried and failed: {tried()}. "
                                        "Plan something different for it: fill types the value "
                                        "into the field, select picks it from a list, press "
                                        "sends keys (tab, arrow keys, enter), or another "
                                        "control. If you see no other way, return a question "
                                        "for the user instead.")
                                escalate = True
                                continue
                            finished = [st for st in plan.steps()
                                        if st.status in ("done", "failed")]
                            if (first["op"] == "click" and finished
                                    and finished[-1].status == "done"
                                    and finished[-1].note == _TOGGLED
                                    and signature(first["op"], first["target"], first["value"],
                                                  first["element"], first["row"])
                                    == signature(finished[-1].op, finished[-1].target,
                                                 finished[-1].value, finished[-1].element, finished[-1].row)):
                                log.info("[INTERVENE] plan rejected — it clicks the same toggle "
                                         "again")
                                note = (f"REJECTED: the step before this one already clicked "
                                        f"'{first['target']}', and all it did was change that "
                                        "control's own appearance: a checkbox or a toggle. "
                                        "Clicking it again undoes it. " + look_further())
                                escalate = True
                                continue
                            if (first["op"] in ("click", "press") and len(finished) >= 2
                                    and all(st.status == "done" and signature(
                                        st.op, st.target, st.value, st.element, st.row) == signature(
                                        first["op"], first["target"], first["value"],
                                        first["element"], first["row"])
                                        for st in finished[-2:])):
                                log.info("[INTERVENE] plan rejected — the same step a third "
                                         "time in a row")
                                note = ("REJECTED: the last two steps were this same step, and "
                                        "the request got no further. A third time will not "
                                        "either. Reach the result another way: a value is typed "
                                        "with fill, also a date (do not click through a "
                                        "calendar); otherwise use another control or ask the "
                                        "user. " + look_further())
                                escalate = True
                                continue
                        # The steps still planned stand if the planner says so and
                        # has not written one of them again (then it wrote the rest
                        # itself). No steps and not final can only mean: carry on.
                        waiting = plan.pending()
                        keep = (bool(waiting)
                                and (reply.get("keep_planned")
                                     or not (reply["steps"] or reply["final"]))
                                and not any(
                                    signature(s["op"], s["target"], s["value"], s["element"],
                                              s["row"])
                                    == signature(w.op, w.target, w.value, w.element, w.row)
                                    for s in reply["steps"] for w in waiting))
                        aside = reply.get("set_aside") or ""
                        if streak and (aside or (keep and not reply["steps"])):
                            # The planner leaves out what failed. Without a word of
                            # its own, the reason is what the last try ended on.
                            leave_open(aside or streak[-1].note)
                        if waiting and not keep:
                            # A planned step it wrote again is the same step: only the
                            # others count as dropped.
                            again = {signature(s["op"], s["target"], s["value"], s["element"],
                                               s["row"]) for s in reply["steps"]}
                            plan.remove([w for w in waiting if signature(
                                w.op, w.target, w.value, w.element, w.row) in again])
                            plan.drop_pending("replanned: the planner wrote the rest anew")
                        # What the planner said of the plan before it was interrupted
                        # holds if the plan is kept.
                        due, was_final, stood = False, keep and stood, False
                        if reply["steps"] or keep:
                            rejected = unproven = 0
                            kept = plan.take_pending()
                            for s in reply["steps"]:
                                added = plan.add(s["op"], s["target"], s["value"], s["element"],
                                                 s["commits"], s["row"])
                                log.info("[TODO] + %s", added.text)
                            plan.put_back(kept)
                            log.info("[TODOS] %s", plan.to_marker())
                            final = reply["final"] or was_final
                            answer = reply["answer"] or (answer if keep else "")
                            note, escalate = "", False
                            continue
                        if not reply["final"]:
                            note = ("REJECTED: your last answer had no steps and final was false, "
                                    "so nothing can happen. Return steps for the current screen, "
                                    "or final true with evidence, or a question.")
                            continue
                        if self._unsaved and not save_asked:
                            save_asked, note = True, _UNSAVED_NOTE
                            log.info("[INTERVENE] completion held back — values were entered "
                                     "and no step saved them")
                            continue
                        if self._batch:
                            # Declared complete while typed values are still unread.
                            self._give_up_unread()
                            late_failures()
                            continue
                        # No steps, final: the planner says the request is complete as
                        # the screen stands. Only on-screen evidence makes that count.
                        passed, details = (
                            check_evidence_in_ocr(reply["evidence"], self._elements)
                            if reply["evidence"] else (False, []))
                        if passed:
                            log_evidence_summary(reply["evidence"], details)
                            self._finish(
                                result(reply["answer"] or answer),
                                "evidence found on screen: " + "; ".join(reply["evidence"]),
                                quoted=reply["evidence"], open_points=plan.open)
                            break
                        log.info("[INTERVENE] completion rejected — evidence not found on screen")
                        unproven += 1
                        if unproven >= 2:
                            # Saying it a third time would not put the evidence on
                            # screen. A person can look and decide.
                            missing = "; ".join(
                                f"'{d['evidence']}'" for d in details if not d["passed"])
                            ask_user(
                                "I believe the request is complete"
                                + (f": {reply['answer']}" if reply["answer"] else "")
                                + ". But I could not confirm it on screen"
                                + (f" (not found: {missing})" if missing else "")
                                + ". Is it complete?",
                                ["Yes, it is complete", "No, keep working"])
                            break
                        note = ("REJECTED: you declared the request complete, but "
                                + (format_evidence_rejection(details) if details
                                   else "gave no evidence.")
                                + " Quote texts exactly as they appear in the element list, or "
                                  "plan the steps that are still missing.")
                        escalate = True
                        continue

                    self._iteration += 1
                    step.status = "in_progress"
                    self._step_id, self._decider_used = step.id, False
                    # [ITER] n always refers to the step that is in_progress in the
                    # [TODOS] line following it; [STEP] names it outright.
                    log.info("[ITER] %d", self._iteration)  # timeline group boundary
                    log.info("[STEP] %s", json.dumps(
                        {"id": step.id, "iter": self._iteration, "op": step.op,
                         "text": step.text, "commits": step.commits},
                        ensure_ascii=False, separators=(",", ":")))
                    log.info("[NAV] → objective: %s", step.text)
                    log.info("[TODOS] %s", plan.to_marker())
                    before = self._elements
                    self._last_change = ChangeSet()
                    t_step = time.monotonic()
                    ok, detail = self._run_step(step)
                    self.executor.tick_click_age()
                    step.seconds = round(time.monotonic() - t_step, 1)
                    step.resolved_by = ("planner" if not ok else "decider"
                                        if self._decider_used or step.planned_by == "decider"
                                        else "code")
                    step.note = " ".join(detail.split())
                    step.detail = "" if self._last_change.is_empty else self._last_change.summary()
                    if step.op in ("fill", "select"):
                        entering = True
                    elif self._last_change.transition == "replaced":
                        entering = False
                    log.info("[PROGRESS] %s", "nochange" if self._elements is before else "changed")
                    if ok:
                        step.status = "done"
                        if step.commits:
                            self._unsaved = []
                        elif (step.op in ("fill", "select")
                              and "already shows the value" not in step.note):
                            self._unsaved.append(step.text)
                        failed_in_a_row = 0
                        forced = False
                        streak.clear()
                        # The decider goes on navigating only while its clicks
                        # change the view. One that opened a popup or changed
                        # something in place was not navigation (it went into
                        # a field, a calendar, a list): the planner's turn.
                        nav_turn = (step.planned_by != "decider"
                                    or self._last_change.transition == "replaced")
                        if not nav_turn:
                            note = (f"The step \"{step.text}\" was chosen by the decision "
                                    "model as a navigation click. Part of the screen changed: "
                                    f"{step.note} Plan from the screen as it is now. If what "
                                    "opened is the helper of a field (a calendar, a list of "
                                    "suggestions), do not work through it: close it with the "
                                    "escape key and enter the value with fill, under the "
                                    "field's label.")
                        log.info("[TODO] ✓ %s", step.text)
                        log.info("[RESULT] verified — %s", step.note)
                        log.info("[ISSUE] ")  # empty clears the UI
                        reason = self._plan_still_fits(step, plan, self._last_change, before)
                        if reason:
                            log.info("[INTERVENE] replan: %s", reason)
                            replan("unforeseen", reason, step, drop=False)
                            note = (reason + ". Read the screen: return what has to happen "
                                    "before the steps still planned, if anything.")
                            final = False
                    else:
                        step.status = "failed"
                        failed_in_a_row += 1
                        log.info("[TODO] ✗ %s", step.text)
                        log.info("[ISSUE] %s", step.note)
                        replan("failed_step", step.note, step, drop=False)
                        streak.append(step)
                        note = (f"The step \"{step.text}\" FAILED: {step.note}. Read the screen "
                                "and reach the same result another way: return the steps that "
                                "take its place.")
                        if len(streak) > 1:
                            note += (f" This is failure number {len(streak)} at this point. "
                                     f"Tried and failed so far: {tried()}. Do not plan any of "
                                     "these again; if you see no other way, leave it out with "
                                     "set_aside if the rest of the request can go on without "
                                     "it, or return a question for the user if it cannot.")
                        escalate, final = True, False
                    late_failures()
                    self._step_id = None
                    log.info("[TODOS] %s", plan.to_marker())
                    if failed_in_a_row >= 3 and not set_aside_or_ask():
                        break     # three ways failed: no fourth
                    if self._iteration >= step_budget and may_go_on("steps"):
                        step_budget += self.cfg.max_steps
                    if self._iteration >= step_budget:
                        last = [st for st in plan.steps() if st.status in ("done", "failed")][-4:]
                        ask_user(
                            f"I have carried out {self._iteration} steps without finishing. "
                            "My last steps: "
                            + "; ".join(f"{st.text} — {st.note}" for st in last)
                            + ". How should I go on? You can also finish this part yourself "
                              "and then tell me to continue.", [])
                        break
                except _TakenOver:
                    # Paused and resumed: whatever was planned was planned for a
                    # screen the user may have changed since.
                    interrupted = step if step is not None and step.status == "in_progress" else None
                    change = self._observe(decaret=True)
                    if interrupted is None and change.is_empty and not change.pixels_changed:
                        # Paused between two steps and the screen is pixel-identical:
                        # nothing happened that the plan does not know about.
                        log.info("screen unchanged during the pause — the plan stands")
                        continue
                    if interrupted is not None:
                        interrupted.status = "skipped"
                        interrupted.note = "interrupted: the agent was paused during this step"
                        log.info("[TODO] − %s (interrupted by a pause)", interrupted.text)
                    self._step_id = None
                    replan("pause", "the agent was paused" + (" during this step"
                                                              if interrupted else ""), interrupted)
                    log.info("[TODOS] %s", plan.to_marker())
                    note = ("The user paused the agent and may have worked on the screen "
                            "themselves. Read the current screen and plan only what is still "
                            "missing.")
                    escalate, final = False, False
        except _StopRequested:
            running = step if step is not None and step.status == "in_progress" else None
            if running is not None:
                running.status = "skipped"
                running.note = "stopped by the user during this step"
            log.info("[STOPPED] %s", json.dumps(
                {"by": self._stop_source() or "key",
                 "step": running.id if running else None,
                 "text": running.text if running else ""},
                ensure_ascii=False, separators=(",", ":")))
            try:
                _stop_flag_path().unlink()
            except OSError:
                pass
            log.info("[TODOS] %s", plan.to_marker())
            self._actions.append({"iteration": self._iteration,
                                  "error": "aborted: stopped by the user"})

        unanswered = ""
        if pending_question and self.cfg.autonomous:
            unanswered = pending_question
            # Not a question to anyone: the reason the run ended unfinished.
            self._actions.append({"iteration": self._iteration,
                                  "error": "aborted: nobody to ask — " + pending_question,
                                  "unanswered": pending_question})
            pending_question, question_options = None, []

        duration = time.time() - start
        log.info("=" * 70)
        log.info("DONE in %dm%.1fs | steps=%d | planner calls=%d | decider calls=%d | "
                 "tokens in/out/total=%d/%d/%d",
                 int(duration // 60), duration % 60, self._iteration,
                 self._calls["planner"], self._calls["decider"],
                 self._tokens["input"], self._tokens["output"], self._tokens["total"])
        log.info("TIME perception=%.1fs (%d full, %d partial, %d unchanged) | planner=%.1fs | "
                 "decider=%.1fs | input=%.1fs",
                 self._timing["perception"], self._parses["full"], self._parses["partial"],
                 self._parses["cached"], self._timing["planner"], self._timing["decider"],
                 self._timing["input"])
        log.info("[TIMING] %s", json.dumps(
            {"total": round(duration, 1), "steps": self._iteration,
             "seconds": {k: round(v, 1) for k, v in self._timing.items()},
             "calls": self._calls, "parses": self._parses, "tokens": self._tokens},
            separators=(",", ":")))
        log.info("=" * 70)

        # Persist the conversation so the next request continues from here.
        if self.cfg.continue_session:
            done = plan.render_done()
            if plan.open and not pending_question:
                done += ("\n" if done else "") + "  NOT DONE:\n" + plan.render_open()
            if unanswered:
                done += (("\n" if done else "")
                         + "  ENDED UNFINISHED, NOBODY COULD BE ASKED: " + unanswered)
            save_session({
                # A request that ended on a question is still open: it is not
                # listed as handled, and the next message is read as its answer.
                "tasks": self._prior_tasks + ([] if pending_question else [request]),
                "history": self._history + ([f"Request: {request}", done] if done else []),
                "open_question": ({"request": request, "question": pending_question,
                                   "kind": question_kind, "open": plan.open}
                                  if pending_question else None),
                # Persist the focused app so follow-up requests re-focus it
                # deterministically. Falls back to the prior value if this run
                # never (re-)focused. Wiped by clear_session on 'New conversation'.
                "target_window": self.executor.target_window or session.get("target_window"),
            })

        return {
            "started_at": start_dt.isoformat(),
            "duration_seconds": duration,
            "actions": self._actions,
            "tokens": self._tokens,
            "question": pending_question,  # non-None if the run ended on a question
            "question_options": question_options,
        }
