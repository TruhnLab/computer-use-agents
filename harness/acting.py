"""Acting and observing: one action, the look at the screen after it, and the
checks on what was typed."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui

from .actions import _MAX_SCROLL_PAGES, _PX_PER_NOTCH, _WHEEL_NOTCH
from .candidates import _MAX_CANDIDATES, Candidate, _owner_label, field_candidates
from .elements import ChangeSet, Element, ElementChange
from .hotkeys import _StopRequested, _TakenOver
from .matching import (
    _cut_label,
    _find_label_elements,
    _is_value_adjacent_to_label,
    _norm_text,
    _value_in_text,
    caret_label,
    check_value_near_label,
    guard_wrong_field,
    shown_value,
    typed_matches,
)
from .paths import _pause_flag_path, _stop_flag_path
from .perception import _BILINEAR, extract_ocr_elements
from .plan import Step
from .prompts import _NONE, Q_COMPLETED, Q_NOTATION
from .screen import _decaret, _foreground_window, capture_foreground_burst

log = logging.getLogger("agent")


class Acting:
    """The part of ComputerAgent that acts, observes and checks what was typed."""

    def _observe(self, decaret: bool = False) -> ChangeSet:
        """Read the screen and remember it as the current observation."""
        t0 = time.monotonic()
        self._shot_b64, self._elements, change = self._parse_screen(decaret=decaret)
        self._timing["perception"] += time.monotonic() - t0
        self._last_change = change
        self._seen_texts |= {el.text for el in self._elements
                             if el.source != "yolo" and (el.text or "").strip()}
        if not change.is_empty or change.caret_xy:
            log.info("[CHANGE] %s", change.summary())
        if self._batch:
            self._read_batch_back(change)
        return change

    def _read_batch_back(self, change: ChangeSet) -> None:
        """The screen was read: check every field typed into since the last
        read. What does not show its value goes to _batch_failed, to be typed
        again the careful way. The values themselves are then taken out of
        the change-set: they are no news to whoever asked for this read."""
        batch, self._batch = self._batch, []
        good = covered = 0
        for step, cand in batch:
            if not _find_label_elements(step.target, self._elements):
                # The field's label is not on screen: something lies over it
                # (a list or a calendar another field opened) or the view has
                # moved on. Whether the value arrived cannot be told from
                # here, and it is certainly no reason to click where the
                # field used to be - that click picked a day in a calendar.
                if change.transition == "replaced":
                    step.checked = True
                    self._batch_failed.append((step, cand, "unread", (
                        f"'{step.value}' was typed into '{step.target}', but the view changed "
                        "before the value could be read back")))
                else:
                    self._batch.append((step, cand))   # read with the next look
                covered += 1
                continue
            verdict, detail = self._check_value(step, change)
            step.checked = True
            if verdict == "pass":
                step.note = " ".join(detail.split())
                self._note_way(step, "typed")
                good += 1
            else:
                self._batch_failed.append((step, cand, verdict, detail))
        log.info("batch: %d entries read back, %d as typed%s", len(batch) - covered, good,
                 f"; {covered} not readable now (label not on screen)" if covered else "")
        values = [step.value for step, _cand in batch]

        def news(changes: List[ElementChange]) -> List[ElementChange]:
            return [c for c in changes
                    if not any(_value_in_text(v, c.element.text) for v in values)]
        change.appeared, change.text_changed = news(change.appeared), news(change.text_changed)

    def _flush_batch(self) -> bool:
        """Read back what the batch typed, now, and put right what did not
        arrive. Called before anything that is not another fast entry: a
        click, a key, a scroll, a field that needs the careful way, the
        planner, the completion check. True if there was something to read."""
        pending = bool(self._batch)
        if pending:
            self._observe(decaret=True)
        self._repair_batch()
        return pending

    def _repair_batch(self) -> None:
        """Type again, the careful way, what a batch did not get into its
        field. What still fails is handed to the run loop (_late): its step
        fails after the fact."""
        failed, self._batch_failed = self._batch_failed, []
        for step, _cand, verdict, detail in failed:
            if verdict == "wrong_field":
                detail += "; it is still there"
            elif verdict != "unread":
                # Where the field is NOW, not where it was when the batch
                # typed: the screen may have moved, or something may lie over
                # that place. Only a field that is certain is typed into again.
                sure = field_candidates(step.target, self._elements)[1]
                if sure is None or sure.tier != 1:
                    detail += ("; not typed again: its field is not certain on the screen as "
                               "it is now")
                else:
                    again, why = self._type_into(step, sure, replace=True)
                    if again == "pass":
                        step.note = " ".join(why.split()) + " (typed a second time)"
                        continue
                    detail = f"{detail}; typed again: {why}"
            self._late.append((step, " ".join(detail.split())))

    def _give_up_unread(self) -> None:
        """Entries still unread when the request is about to be declared
        complete: their steps fail, so that the planner looks at them."""
        for step, _cand in self._batch:
            step.checked = True
            self._late.append((step, (f"'{step.value}' was typed into '{step.target}', but it "
                                      "could not be read back: the label is not on screen")))
        self._batch = []

    def _stop_source(self) -> Optional[str]:
        """'key' or 'ui' if the operator asked to stop, else None."""
        if self._hotkeys.stop_requested.is_set():
            return "key"
        if _stop_flag_path().exists():
            return "ui"
        return None

    def _checkpoint(self) -> None:
        """Honour the operator's stop (key or flag) and the pause flag. Called
        before every step and before every input action, so after either the
        agent does not touch mouse or keyboard again. A pause ends in
        _TakenOver: the user may have worked on the screen, so the caller reads
        it afresh."""
        if self._stop_source():
            raise _StopRequested()
        flag = _pause_flag_path()
        if not flag.exists():
            # Everything the agent reads and does goes to the window in front.
            # If that is no longer a window of its application (a person
            # clicked elsewhere, another program came up), it must not go on -
            # and it must not pull the focus back either: someone may be
            # typing there. It pauses, like after a pointer move.
            front = _foreground_window()
            if self._target_pid is None or front is None or front[0] == self._target_pid:
                return
            if self.cfg.autonomous:
                # Nobody is typing there, and nobody would resume: back to the
                # application, and the screen is read afresh as after a pause.
                log.info("[FOCUS] another window came to the front ('%s') and nobody attends "
                         "the run \u2192 %s", front[1][:60],
                         self.executor._focus_window(self.executor.target_window)
                         if self.executor.target_window else "no window to return to")
                self._pointer = None
                raise _TakenOver()
            flag.write_text("another application's window came to the front: "
                            f"'{front[1][:60]}'", encoding="utf-8")
        try:
            reason = flag.read_text(encoding="utf-8").strip()
        except OSError:
            reason = ""
        log.info("[PAUSED] %s", reason or "paused")
        while flag.exists():
            if self._stop_source():
                raise _StopRequested()
            time.sleep(0.2)
        log.info("[RESUMED]")
        # The person has had the mouse (if only to click Resume): the pointer
        # is theirs to leave anywhere. Kept at the agent's last position, it
        # was "moved by hand" again at the next action, and the run paused
        # after every resume.
        self._pointer = None
        if self.executor.target_window:   # back to the application the run works in
            log.info("[FOCUS] %r \u2192 %s", self.executor.target_window,
                     self.executor._focus_window(self.executor.target_window))
        raise _TakenOver()

    def _guard(self) -> None:
        """Before the agent touches mouse or keyboard: honour stop and pause,
        and pause if a person has moved the pointer since the last action."""
        self._checkpoint()
        if (self.cfg.pause_on_pointer_move and not self.cfg.autonomous
                and self._pointer is not None):
            x, y = pyautogui.position()
            if math.hypot(x - self._pointer[0], y - self._pointer[1]) > 12:
                # Not where the agent left it: a person has taken the mouse.
                _pause_flag_path().write_text("the pointer was moved by hand", encoding="utf-8")
                self._checkpoint()

    def _act(self, action: str, args: Dict[str, Any]) -> str:
        self._guard()
        log.info("[TOOL] → %s(%s)", action, args)
        # The same action, machine-readable and tied to its step, for the UI.
        # Logged BEFORE it runs: a run cut off mid-action still shows what it was doing.
        log.info("[ACTION] %s", json.dumps(
            {"step": self._step_id, "action": action, "args": args},
            ensure_ascii=False, separators=(",", ":")))
        t0 = time.monotonic()
        result = self.executor.execute(action, args)
        self._pointer = tuple(pyautogui.position())
        self._timing["input"] += time.monotonic() - t0
        log.info("[RESULT] %s", result)
        self._actions.append({"iteration": self._iteration, "action": action,
                              "args": args, "result": result})
        return result

    def _focused_label(self) -> Optional[str]:
        """Text of the label next to the blinking caret, '' if a caret is seen
        but no label can be resolved, None if no caret is seen."""
        seen, label = self._caret_owner()
        if not seen:
            return None
        return label.text.strip() if label is not None else ""

    def _caret_owner(self) -> Tuple[bool, Optional[Element]]:
        """(a caret is seen, the label element next to it or None)."""
        t0 = time.monotonic()
        frames, (win_x, win_y, _w, _h) = capture_foreground_burst()
        _healed, carets = _decaret(frames)
        self._timing["perception"] += time.monotonic() - t0
        if not carets:
            return False, None
        return True, caret_label((carets[0][0] + win_x, carets[0][1] + win_y), self._elements)

    def _is_label_of(self, step: Step, label: Optional[Element]) -> bool:
        """Is *label* the label the step names? Decided on the element, not
        on its text: next to 'Prescription' there is a 'Prescription Date',
        whose text contains the other's."""
        return label is not None and any(
            label is found for found in _find_label_elements(step.target, self._elements))

    def _pick(self, step: Step, cands: List[Candidate], sure: Optional[Candidate],
              question: str, limit: int = _MAX_CANDIDATES) -> Optional[Candidate]:
        """Choose the candidate a step means. An exact, unique match needs no
        model; everything else is the decider's choice. What it picks is
        clicked or typed into without a second opinion, so it has to be as
        sure as for a navigation click (in the test runs its picks below 0.6
        were all wrong, its right ones were above 0.8); an unsure first answer
        is asked again with the screenshot. When it stays unsure, the exact
        match stands if there is one, else nothing is picked and the planner
        gets the step back."""
        if not cands:
            return None
        if sure is not None and not self.cfg.decider_always:
            return sure
        shown = cands[:limit]
        listing = "\n".join(f"  {c.key}: {c.desc}" for c in shown)
        said = (f"THE PLANNER ABOUT THIS PLAN: {self._last_assessment}\n\n"
                if self._last_assessment else "")
        choice = self._ask(self._state(step, said + "CANDIDATES:\n" + listing), "target",
                           question, [c.key for c in shown] + [_NONE],
                           labels={c.key: c.text.strip() or c.desc for c in shown},
                           describe={**{c.key: c.desc for c in shown},
                                     _NONE: "no candidate fits"},
                           min_conf=self.cfg.decider_nav_conf)
        if choice is None:
            return sure
        return next((c for c in shown if c.key == choice), None)

    def _read_again(self, el: Element) -> str:
        """Read one element's text once more, enlarged: a second opinion before
        a typed value is called wrong because OCR lost a thin character."""
        if self._prev_screenshot is None or self._last_rect is None:
            return ""
        win_x, win_y = self._last_rect[0], self._last_rect[1]
        box = (max(0, el.x - win_x - 6), max(0, el.y - win_y - 6),
               el.x - win_x + el.width + 6, el.y - win_y + el.height + 6)
        crop = self._prev_screenshot.crop(box)
        crop = crop.resize((crop.width * 3, crop.height * 3), _BILINEAR)
        return " ".join(e.text.strip() for e in sorted(
            extract_ocr_elements(crop, crop=True), key=lambda e: (e.y // 20, e.x)))

    def _check_value(self, step: Step, change: ChangeSet, offered: str = "") -> Tuple[str, str]:
        """Did the step's value arrive in its field? Returns (verdict, detail):
        'pass' | 'mismatch' (the field shows something else) | 'wrong_field' |
        'missing'. *offered* is the text of a suggestion that was taken for
        the value: the field showing exactly that is the value arrived."""
        verdict, detail = check_value_near_label(step.value, step.target, self._elements)
        found = shown_value(step.value, step.target, self._elements) if verdict == "pass" else None
        if verdict == "pass" and found is None:
            return "pass", detail
        if found is None:
            alarm = guard_wrong_field(change, step, self._elements)
            if alarm:
                return "wrong_field", alarm
            mutations = change.text_changed + change.appeared
            if verdict == "no_label" and any(
                    _value_in_text(step.value, c.element.text) for c in mutations):
                return "pass", "value appeared on screen (its label is not readable)"
            # The value is not found next to its label. What the field shows
            # instead is the text that just changed there.
            label_els = _find_label_elements(step.target, self._elements)
            near = [c.element for c in mutations
                    if c.element.source != "yolo" and (c.element.text or "").strip()
                    and any(_is_value_adjacent_to_label(c.element, l_el) for l_el in label_els)]
            if not near:
                return "missing", detail
            found = (near[0].text.strip(), near[0])
        shown, el = found
        same, diff = typed_matches(step.value, shown)
        if not same:
            again = self._read_again(el)
            same = bool(again) and typed_matches(
                step.value, _cut_label(step.target, again))[0]
        if same:
            return "pass", detail if verdict == "pass" else f"'{shown}' found next to '{step.target}'"
        if offered and typed_matches(offered, shown)[0]:
            return "pass", (f"the field '{step.target}' shows '{shown}', the option it "
                            f"offered for '{step.value}'")
        cut, whole = "".join(shown.split()).casefold(), "".join(offered.split()).casefold()
        if offered and len(cut) >= 4 and whole.startswith(cut):
            # A field too narrow for the option that was clicked shows its
            # beginning.
            return "pass", (f"the field '{step.target}' shows '{shown}', the beginning of the "
                            f"option chosen ('{offered}')")
        # An application may show a number or a date in its own notation
        # ('01/01/2024' as '1/1/2024'). Only when nothing but digits and
        # separators differ is that worth a question; a letter or a sign such
        # as '@' that is missing is simply missing.
        differing = "".join(re.findall(r"'([^']*)'", diff))
        if differing and all(ch in "0123456789.,/-: ()+" for ch in differing):
            state = self._state(step, f"TYPED: '{step.value}'\nTHE FIELD NOW SHOWS: '{shown}'")
            if self._ask(state, "outcome", Q_NOTATION, ["yes", "no"]) == "yes":
                return "pass", f"the field shows '{shown}', the same value in its own notation"
        typed, has = "".join(step.value.split()).casefold(), "".join(shown.split()).casefold()
        if typed and has.startswith(typed):
            # The field added to what was typed: an entry it knows, completed.
            # Typing it again would end the same way; is it the entry meant?
            state = self._state(step, f"TYPED: '{step.value}'\nTHE FIELD NOW SHOWS: '{shown}'")
            if self._ask(state, "outcome", Q_COMPLETED, ["yes", "no"]) == "yes":
                return "pass", (f"the field '{step.target}' completed '{step.value}' to "
                                f"'{shown}'")
        return "mismatch", (f"'{step.value}' was typed, but the field shows '{shown}' "
                            f"({diff})")

    def _type_into(self, step: Step, cand: Candidate, replace: bool) -> Tuple[str, str]:
        """Click a field and type the step's value. Returns (verdict, detail)
        with the verdicts of _check_value plus 'focus' (nothing typed: the
        caret sat next to another label)."""
        self._act("click", {"x": cand.x, "y": cand.y})
        self._last_target = step.target
        # Select-all + type overwrites whatever holds the focus, and a place
        # that is not the own control of an exactly matching label may belong
        # to another field: in both cases the text cursor has to be next to
        # the label before anything is typed.
        if (replace or cand.tier != 1 or not cand.exact) and self.cfg.focus_check:
            seen, owner = self._caret_owner()
            if seen and owner is not None and not self._is_label_of(step, owner):
                return "focus", (f"the click meant for '{step.target}' put the caret "
                                 f"next to '{owner.text.strip()}'")
            if not seen and (cand.tier != 1 or not cand.exact):
                # Not the label's own control, and no text cursor to prove
                # it: typing would go nobody knows where.
                return "nocursor", (f"the place chosen for '{step.target}' took no text "
                                    "cursor when it was clicked")
        if replace:
            self._act("keypress", {"keys": ["ctrl", "a"]})
        self._act("type_text", {"text": step.value})
        return self._typed_result(step, cand, appended=not replace)

    def _typed_result(self, step: Step, cand: Candidate, appended: bool = False,
                      change: Optional[ChangeSet] = None) -> Tuple[str, str]:
        """Read the screen after a value was typed, check the value, and deal
        with what the field opened while it was typed into. *appended*: the
        value was typed at the text cursor without select-all, so backspaces
        take exactly it back. *change*: the screen has been read already."""
        if change is not None:
            return self._judge_typed(step, cand, appended, change)
        # A field may open a helper as it is typed into — a list of
        # suggestions, a calendar — a moment after the last key. The frames for
        # the caret removal must be taken after that: a helper appearing
        # between them is "healed" out of the picture like the caret, and is
        # then neither seen nor closed while it covers the next field.
        self._settle(expect_large=False, quiet=2)
        # Decaret: a caret right after the typed text reads as an extra glyph
        # ('Doe|' → 'Doel') and would fail a correct entry.
        return self._judge_typed(step, cand, appended, self._observe(decaret=True))

    def _judge_typed(self, step: Step, cand: Candidate, appended: bool,
                     change: ChangeSet) -> Tuple[str, str]:
        taken = self._take_suggestion(step, change)
        if taken:
            return self._check_value(step, self._last_change, offered=taken)
        verdict, detail = self._check_value(step, change)
        if verdict == "pass" and not self._close_helper(step, cand, change):
            return "missing", ("the value was typed, but closing the list that opened with "
                               "Escape removed it again")
        if verdict in ("missing", "wrong_field") and appended and change.caret_xy is not None:
            # Not next to its label - and the text cursor, which sits right
            # behind what was typed, is next to another label: both say the
            # value went into that other field. Take it back.
            other = caret_label(change.caret_xy, self._elements)
            if other is not None and not self._is_label_of(step, other):
                return "missing", self._take_back(step, other.text.strip())
        return verdict, detail

    def _take_back(self, step: Step, where: str) -> str:
        """Delete what was just typed into the wrong field: one backspace per
        character, from the text cursor that still sits behind it. Returns the
        sentence for the step's note."""
        log.warning("'%s' went into the field next to '%s' — deleting it there", step.value, where)
        self._act("delete_chars", {"count": len(step.value), "from_caret": True})
        self._settle(expect_large=False)
        self._observe(decaret=True)
        return (f"'{step.value}' went into the field next to '{where}', not next to "
                f"'{step.target}'; its {len(step.value)} characters were deleted there again")

    def _close_helper(self, step: Step, cand: Candidate, change: ChangeSet) -> bool:
        """Close a helper the field left open over the fields around it (a
        calendar, a list without the exact value): new texts below the field,
        or above it where there was no room below - two, or one if the screen
        shows a popup. A popup without any text
        below the field is something else (a notification, another window). Escape is the key every
        such helper understands — but in some applications it also discards
        the entry or closes the form, so the value is looked for again
        afterwards. False if it is gone; Escape is then not used again in this
        run."""
        helper = [c.element for c in change.appeared
                  if c.element.source != "yolo" and (c.element.text or "").strip()
                  and not _value_in_text(step.value, c.element.text)
                  and abs(c.element.center_x - cand.x) <= 400
                  and (0 < c.element.center_y - cand.y <= 450
                       or -450 <= c.element.center_y - cand.y < -20)]
        if len(helper) < (1 if change.transition == "popup" else 2) or self._escape_unsafe:
            return True
        log.info("the field opened a helper (%d new texts next to it) — closing it with "
                 "Escape", len(helper))
        self._act("keypress", {"keys": ["escape"]})
        # The helper closes and the form is redrawn (a date is rewritten, an
        # age appears): read while that is going on, the field comes back
        # without its text and the value counts as removed.
        self._settle(expect_large=False, quiet=2)
        after = self._observe(decaret=True)
        gone = check_value_near_label(step.value, step.target, self._elements)[0] == "fail"
        if gone and after.transition != "replaced":
            # Not found as typed. Leaving the field may only have made the
            # application rewrite it ('01/01/1990' as '1/1/1990'): judged as
            # after typing, where the same value in another notation counts.
            # Taken for removed, the date was typed a second time, the
            # calendar opened again and stayed open.
            gone = self._check_value(step, after)[0] != "pass"
        if after.transition == "replaced" or gone:
            self._escape_unsafe = True
            log.warning("Escape removed the typed value — not using it to close helpers again")
            return False
        return True

    def _scroll_page(self, down: bool, pages: int = 1) -> ChangeSet:
        """Move the view by about 70% of the window's height per page, in one
        movement, and read it."""
        args: Dict[str, Any] = {}
        height = self.height
        if self._last_rect is not None:   # scroll inside the application window
            x, y, w, height = self._last_rect
            args.update(x=x + w // 2, y=y + height // 2)
        notches = max(3, round(0.7 * height / _PX_PER_NOTCH)) * pages
        args["scroll_clicks"] = (-1 if down else 1) * notches * _WHEEL_NOTCH
        self._act("scroll", args)
        self._settle(expect_large=False)
        return self._observe()

    def _scroll_to(self, found) -> bool:
        """Look for a target outside the visible part: page by page down to the
        end, then up to the top, until found() says it is on screen. If it is
        nowhere, the view is brought back to about where it was (the same
        number of pages, in one movement), so that a failed search does not
        leave the planner on a different part of the page."""
        for down in (True, False):
            pages = 0
            for _ in range(_MAX_SCROLL_PAGES):
                if not self._scroll_page(down).pixels_changed:
                    break
                pages += 1
                if found():
                    return True
            if pages:
                self._scroll_page(not down, pages)
        return False

    def _named_by_content(self, step: Step) -> str:
        """A planner sometimes names a field by what it shows ('10/10/2000')
        instead of by its label. Once a value is typed that content is gone,
        and nothing could tell whether the new value arrived. If the target is
        exactly the text inside one detected control and written nowhere else
        on the screen, the step is not carried out: the returned note says so
        and names the label next to that control - as information for the
        planner, who has to say which field is meant. '' if the target is not
        such a text."""
        if step.target.strip().endswith(("*", ":")):
            return ""   # written like a label
        want = _norm_text(step.target)
        texts = [el for el in self._elements if el.source != "yolo" and _norm_text(el.text) == want]
        if len(texts) != 1 or not texts[0].interactive:
            return ""
        owner = _owner_label(texts[0], self._elements)
        if owner is None or not (owner.text or "").strip():
            return ""
        # A detected box around a real label makes the label look like the
        # content of a field. It is content only if the field found for the
        # label next to it is this very text.
        _cands, own = field_candidates(owner.text, self._elements)
        if own is None or own.key != texts[0].stable_id:
            return ""
        hint = f" The label next to it reads '{owner.text.strip()}'."
        return (f"'{step.target}' is what a field shows, not the field's label. Name the field "
                f"by its label.{hint}")
