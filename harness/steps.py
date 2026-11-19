"""Carrying out the steps of a plan: fill, select, click, drag, type, press, scroll,
wait and check."""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

import pyautogui
from PIL import Image

from .actions import _EDGE_STRIP, _KEY_MAP, _WAIT_MAX_S
from .candidates import _MAX_OPTIONS, Candidate, field_candidates, text_candidates
from .elements import ChangeSet, Element
from .matching import (
    _find_label_elements,
    _norm_text,
    _same_row,
    _value_in_text,
    caret_label,
)
from .plan import _POINT_SPEC, Step, _click_kind, _parse_keys
from .prompts import _TOGGLED, _TYPED_UNREAD, Q_OPTION, Q_OUTCOME, Q_REFUSED, Q_TARGET
from .screen import _changed_cells, capture_foreground, typing_only_changed

log = logging.getLogger("agent")


class Steps:
    """The part of ComputerAgent that carries out one step of the plan."""

    def _locate_field(self, step: Step) -> Tuple[Optional[Candidate], str]:
        """The place to click for the field a fill/select step names, or
        (None, reason). The field is looked for further down the page if its
        label is not on screen. Where the detector saw no control next to the
        label, the label itself is clicked — in many applications that puts
        the text cursor into its field — and the cursor has to prove it."""
        self._need_controls()
        named = self._named_by_content(step)
        if named:
            return None, named
        cands, sure = field_candidates(step.target, self._elements)
        if not cands:
            if not self._scroll_to(lambda: bool(_find_label_elements(step.target, self._elements))):
                return None, (f"the label '{step.target}' is not on this page, neither "
                              "further down nor further up")
            self._need_controls()
            cands, sure = field_candidates(step.target, self._elements)
        if cands and not any(c.tier == 1 for c in cands) and self._controls_ready:
            # The controls were detected when a step first needed them, and
            # since then only where the screen changed. Something that lay
            # over this field at that moment (an open list) hides it for good:
            # look at the whole window once more before giving the field up.
            log.info("no control of its own found for '%s' — detecting the controls on the "
                     "whole window once more", step.target)
            self._controls_ready = False
            self._need_controls()
            cands, sure = field_candidates(step.target, self._elements)
        if not any(c.tier == 1 for c in cands) and self._last_rect is not None:
            # No control of its own next to the label, and the label sits at
            # the lower edge of the window: its field is probably cut off
            # there. Bring it into view before anything is clicked.
            bottom = self._last_rect[1] + self._last_rect[3]
            if any(c.tier == 3 and c.exact and bottom - c.y <= _EDGE_STRIP for c in cands):
                log.info("'%s' is at the lower edge of the window — scrolling its field "
                         "into view", step.target)
                if self._scroll_page(True).pixels_changed:
                    self._need_controls()
                    cands, sure = field_candidates(step.target, self._elements)
        if any(c.tier <= 2 and _norm_text(c.text) == _norm_text(step.value) for c in cands):
            return None, ""   # nothing to do: the field already shows the value
        cand = None
        if any(c.tier <= 2 for c in cands):
            cand = self._pick(step, cands, sure, Q_TARGET)
        if cand is not None:
            return cand, ""
        labels = [c for c in cands if c.tier == 3 and c.exact]
        if len(labels) != 1:
            return None, f"no field for the label '{step.target}' was detected on screen"
        log.info("no control detected next to '%s' — clicking the label to reach its field",
                 step.target)
        self._act("click", {"x": labels[0].x, "y": labels[0].y})
        # Leaving the previous field may make it validate and move the layout:
        # the screen is read again before the text cursor is looked up.
        self._settle(expect_large=False)
        change = self._observe(decaret=True)
        holder = (caret_label(change.caret_xy, self._elements)
                  if change.caret_xy is not None else None)
        if self._is_label_of(step, holder):
            now = [c for c in field_candidates(step.target, self._elements)[0]
                   if c.tier == 3 and c.exact]
            return (now[0] if len(now) == 1 else labels[0]), ""
        return None, (f"the field for '{step.target}' was not detected on screen, and clicking "
                      "its label put no text cursor into it")

    def _fast_field(self, step: Step
                    ) -> Optional[Tuple[Candidate, List[Tuple[int, int, int, int]]]]:
        """The field of a fill step, if it can be typed into without reading
        the screen afterwards: exactly one label matches exactly, exactly one
        control is its own, and that control is an empty detected box, fully
        inside the window. Returns the candidate and the screen boxes that
        typing into it may change: the control and its label."""
        if not self.cfg.batch_entry or not self._controls_ready or self._last_rect is None:
            return None
        if self._named_by_content(step):
            return None
        _cands, sure = field_candidates(step.target, self._elements)
        if sure is None or sure.tier != 1 or not sure.exact or sure.has_text:
            return None
        box = next((el for el in self._elements
                    if el.stable_id == sure.key and el.source == "yolo"), None)
        labels = _find_label_elements(step.target, self._elements)
        if box is None or len(labels) != 1:
            return None
        if self._last_rect[1] + self._last_rect[3] - (box.y + box.height) < 4:
            return None   # cut off at the window's lower edge
        return sure, [(el.x, el.y, el.x + el.width, el.y + el.height)
                      for el in (box, labels[0])]

    _last_target = ""   # label of the field typed into last (it holds the focus)

    def _field_boxes(self, target: str) -> List[Tuple[int, int, int, int]]:
        """The screen boxes of the field with this label, on the screen as it
        is now: its label and the controls of its own."""
        if not target:
            return []
        boxes = [(el.x, el.y, el.x + el.width, el.y + el.height)
                 for el in _find_label_elements(target, self._elements)]
        keys = {c.key for c in field_candidates(target, self._elements)[0] if c.tier == 1}
        for el in self._elements:
            if el.stable_id in keys:
                boxes.append(el.control_rect if el.source != "yolo" and el.control_rect
                             else (el.x, el.y, el.x + el.width, el.y + el.height))
        return boxes

    def _clicked_box(self) -> List[Tuple[int, int, int, int]]:
        """The box of the control the agent clicked last, grown by the padding
        a button has around its text; [] if nothing is there any more."""
        point = self.executor.last_click_point
        if point is None:
            return []
        px, py = point
        under = [el for el in self._elements
                 if el.x <= px <= el.x + el.width and el.y <= py <= el.y + el.height]
        if not under:
            return []
        el = min(under, key=lambda e: e.width * e.height)
        x1, y1, x2, y2 = (el.control_rect if el.source != "yolo" and el.control_rect
                          else (el.x, el.y, el.x + el.width, el.y + el.height))
        return [(x1 - 24, y1 - 24, x2 + 24, y2 + 24)]

    def _typing_cells(self, before: Image.Image, after: Image.Image
                      ) -> Optional[List[Tuple[int, int, int, int]]]:
        """What changed between two frames, for the fast entry: without the
        differences nobody can see (a colour step of a few of 255, a lone
        pixel), which made it stop and read the screen for nothing. Only
        here - what is read again after a change is decided exactly."""
        return _changed_cells(before, after, tolerance=self.cfg.batch_color_tolerance,
                              drop_lone=True)

    def _type_unread(self, step: Step, cand: Candidate,
                     boxes: List[Tuple[int, int, int, int]]) -> bool:
        """Click a field and type the step's value, then compare the screen
        with how it looked before: True if everything that changed lies
        inside *boxes* or where the focus was until now, and something did
        change in this field. No OCR."""
        self._guard()
        # Besides the field and its label, typing changes the place that had
        # the focus until now: the field typed into before, or the control
        # clicked last (a button that opened this form).
        allowed = boxes + self._field_boxes(self._last_target) + self._clicked_box()
        self._last_target = step.target
        # The pointer goes first and the reference frame is taken after it:
        # what changes because the pointer left one control and hovers over
        # another is not part of what typing changes.
        self.executor._glide(cand.x, cand.y)
        pyautogui.moveTo(cand.x, cand.y)
        self._pointer = tuple(pyautogui.position())
        # ... and it is taken when the screen is still: a control the pointer
        # has just left may still be fading out of its hover colour.
        before, rect = capture_foreground()
        for _ in range(6):
            time.sleep(0.1)
            frame, frame_rect = capture_foreground()
            still = frame_rect == rect and self._typing_cells(before, frame) == []
            before, rect = frame, frame_rect
            if still:
                break
        self._act("click", {"x": cand.x, "y": cand.y})
        self._act("type_text", {"text": step.value})
        time.sleep(self.cfg.batch_settle_s)
        after, rect_after = capture_foreground()
        if rect != rect_after:
            return False
        cells = self._typing_cells(before, after)
        if cells is None:
            return False
        ok, why = typing_only_changed(
            [(x1 + rect[0], y1 + rect[1], x2 + rect[0], y2 + rect[1])
             for x1, y1, x2, y2 in cells], boxes[0], allowed)
        if not ok:
            log.info("after typing into '%s' %s — reading the screen", step.target, why)
        return ok

    def _do_fill(self, step: Step) -> Tuple[bool, str]:
        if self._batch_failed:
            self._repair_batch()
        if self._way(step) == "list":
            log.info("'%s' was entered by choosing from a list earlier in this request — "
                     "opening its list at once", step.target)
            self._flush_batch()
            return self._do_select(step)
        self._need_controls()
        fast = self._fast_field(step)
        if fast is None:
            self._flush_batch()
            cand, why = self._locate_field(step)
            if cand is None:
                return (False, why) if why else (True, "the field already shows the value")
            # A field reached through its label holds unknown content: replace it
            # (the label candidate has text, and the focus check guards the replace).
            verdict, detail = self._type_into(step, cand, replace=cand.has_text)
            return self._settle_fill(step, cand, verdict, detail)
        cand, boxes = fast
        if self._type_unread(step, cand, boxes):
            self._batch.append((step, cand))
            step.checked = False
            return True, _TYPED_UNREAD
        # Something besides the field changed, or nothing did. Read the screen
        # now: that read also checks the fields typed into before this one,
        # and this one goes on the careful way from here.
        self._settle(expect_large=False, quiet=2)
        change = self._observe(decaret=True)
        verdict, detail = self._typed_result(step, cand, appended=True, change=change)
        result = self._settle_fill(step, cand, verdict, detail)
        self._repair_batch()
        return result

    def _settle_fill(self, step: Step, cand: Candidate, verdict: str,
                     detail: str) -> Tuple[bool, str]:
        """What follows the first attempt to type a value, by its verdict."""
        if verdict == "nocursor":
            # No text cursor: not a field to type into. If the click opened a
            # list of options there, the value is one of them.
            opened = self._opened_near(self._observe(), cand)
            if opened:
                log.info("the field opened a list instead of taking a text cursor — "
                         "choosing the option")
                ok, note = self._choose_option(step, opened)
                if ok:
                    self._note_way(step, "list")
                return ok, note
            return False, detail + ", and no list of options opened; nothing was typed"
        if verdict == "focus":
            return False, detail + "; nothing was typed"
        if verdict in ("missing", "mismatch"):
            # Nothing arrived (the click probably missed the field), or not
            # everything did. Once more, replacing whatever the field holds.
            first = detail
            verdict, detail = self._type_into(step, cand, replace=True)
            if verdict == "mismatch":
                detail += ", also at the second attempt"
            elif verdict in ("focus", "nocursor"):
                detail += "; nothing was typed this time. At the first attempt: " + first
        if verdict == "wrong_field":
            detail += self._remove_stray(step, cand)
        elif verdict == "missing":
            # Typing can still have changed the field (a list field jumps to
            # an option by its first letters): say what it shows now.
            shown = [c.text.strip() for c in field_candidates(step.target, self._elements)[0]
                     if c.tier == 1 and c.text.strip()]
            if shown:
                detail += f"; the field '{step.target}' now shows '{shown[0][:60]}'"
        if verdict == "pass":
            self._note_way(step, "typed")
        return verdict == "pass", detail

    def _remove_stray(self, step: Step, cand: Candidate) -> str:
        """The value went into another field than the step's. The text cursor
        is still in it: if the place clicked was empty before, select-all +
        delete puts that field back as it was, and the screen has to show that
        the value is gone. Returns the rest of the sentence for the step's
        note — the planner must know whether a stray entry is left behind."""
        if cand.has_text:
            return ("; it is still there, and what that field held before may have been "
                    "changed: put it right")
        self._act("keypress", {"keys": ["ctrl", "a"]})
        self._act("keypress", {"keys": ["delete"]})
        self._settle(expect_large=False)
        self._observe(decaret=True)
        if any(el.source != "yolo" and _value_in_text(step.value, el.text)
               for el in self._elements):
            return "; removing it there did not work: it is still on screen and has to be removed"
        return "; it was removed there again"

    def _choose_option(self, step: Step, opened: List[Element]) -> Tuple[bool, str]:
        """Click the option of an opened list that the step asks for."""
        options, sure = text_candidates(step.value, opened)
        if sure is None:
            # No option is exactly the value. If exactly one begins with it
            # as whole words ('11/16/2026' -> '11/16/2026 (Followup)', not
            # '10/14/2025 - 11/16/2026 (Imaging)'), or failing that exactly
            # one contains it, that is the one - no need to ask.
            want = re.findall(r"[^\W_]+", step.value.casefold())
            holding = [el for el in opened if want and re.findall(
                r"[^\W_]+", (el.text or "").casefold())[:len(want)] == want]
            if not holding:
                holding = [el for el in opened if _value_in_text(step.value, el.text)]
            if len(holding) == 1:
                sure = next((c for c in options if c.key == holding[0].stable_id), None)
                if sure is None:
                    sure = Candidate(key=holding[0].stable_id, x=holding[0].center_x,
                                     y=holding[0].center_y, desc="", text=holding[0].text)
                    options.insert(0, sure)
        if not options:   # no option resembles the value: the decider reads the whole list
            options = [Candidate(key=el.stable_id, x=el.center_x, y=el.center_y, desc="",
                                 text=el.text) for el in opened]
        for c in options:
            c.desc = f"option '{c.text.strip()[:60]}'"
        option = self._pick(step, options, sure, Q_OPTION, limit=_MAX_OPTIONS)
        if option is None:
            self._act("keypress", {"keys": ["escape"]})
            self._observe()
            shown = ", ".join(f"'{el.text.strip()[:30]}'" for el in opened[:12])
            return False, (f"the option '{step.value}' is not among the options of "
                           f"'{step.target}': {shown}")
        self._act("click", {"x": option.x, "y": option.y})
        self._settle(expect_large=False)
        verdict, detail = self._check_value(step, self._observe(), offered=option.text.strip())
        return verdict == "pass", detail

    def _take_suggestion(self, step: Step, change: ChangeSet) -> str:
        """A field that suggests values while typing leaves its list open over
        the fields below it. The suggestion for the typed value is clicked:
        that confirms the entry and closes the list. It is the one that is
        exactly the value; failing that the only one that contains the value
        as whole words ('test' -> 'test - null (10 available)'), or the
        decider's choice among several of those. Only a text that lies
        entirely below the text cursor counts - not the typed text in the
        field itself, and not an echo of the value elsewhere on the screen.
        Returns the text of the suggestion taken, '' if none was."""
        if change.caret_xy is None:
            return ""
        cx, cy = change.caret_xy
        want = _norm_text(step.value)
        below = [c.element for c in change.appeared + change.text_changed
                 if c.element.source != "yolo" and (c.element.text or "").strip()
                 and c.element.y > cy and c.element.center_y - cy <= 300
                 and abs(c.element.center_x - cx) <= 400]
        exact = [el for el in below if _norm_text(el.text) == want]
        holding = [el for el in below if _value_in_text(step.value, el.text)]
        if len(exact) == 1:
            taken = exact[0]
        elif not exact and len(holding) == 1:
            taken = holding[0]
        elif not exact and len(holding) > 1:
            options = [Candidate(key=el.stable_id, x=el.center_x, y=el.center_y,
                                 desc=f"option '{el.text.strip()[:60]}'", text=el.text)
                       for el in holding]
            option = self._pick(step, options, None, Q_OPTION, limit=_MAX_OPTIONS)
            taken = next((el for el in holding if option and el.stable_id == option.key), None)
            if taken is None:
                return ""
        else:
            return ""
        self._act("click", {"x": taken.center_x, "y": taken.center_y})
        self._settle(expect_large=False)
        self._observe(decaret=True)
        return taken.text.strip()

    @staticmethod
    def _opened_near(change: ChangeSet, cand: Candidate) -> List[Element]:
        """The texts that appeared near a field when it was clicked: the
        options of a list it opened. A text that changed somewhere else on
        the screen is not one."""
        return [c.element for c in change.appeared
                if c.element.source != "yolo" and (c.element.text or "").strip()
                and abs(c.element.center_x - cand.x) <= 400
                and abs(c.element.center_y - cand.y) <= 450]

    def _do_select(self, step: Step) -> Tuple[bool, str]:
        """Choose a value in a field. The planner guesses the kind of field from
        its label; what the field does when clicked decides: a list opens → the
        option is clicked; no list, but a text cursor → the value is typed (a
        plain field, or one that suggests values while typing)."""
        if self._way(step) == "typed":
            log.info("'%s' was entered by typing earlier in this request — typing at once",
                     step.target)
            return self._do_fill(step)
        cand, why = self._locate_field(step)
        if cand is None:
            return (False, why) if why else (True, "the field already shows the value")
        self._act("click", {"x": cand.x, "y": cand.y})
        self._last_target = step.target
        self._settle(expect_large=False)
        change = self._observe(decaret=True)
        if change.caret_xy is None:
            # No text cursor: not a field to type into. A list of options is
            # what appeared near the field; a text that changed somewhere else
            # on the screen is not one.
            opened = self._opened_near(change, cand)
            if opened:
                ok, note = self._choose_option(step, opened)
                if ok:
                    self._note_way(step, "list")
                return ok, note
            return False, (f"clicking the field '{step.target}' opened no list of options and "
                           "put no text cursor in it")
        # A text cursor: the field takes typed text, whether it is a plain
        # field or one that offers a list to pick from while typing.
        log.info("the field took a text cursor — typing the value")
        self._act("type_text", {"text": step.value})
        verdict, detail = self._typed_result(step, cand)
        if verdict == "wrong_field":
            detail += self._remove_stray(step, cand)
        if verdict == "pass":
            self._note_way(step, "typed")
            return True, detail + " (typed into the field)"
        return False, (f"the field '{step.target}' took a text cursor, so '{step.value}' was "
                       f"typed, but {detail}")

    def _row_anchors(self, row: str) -> List[Element]:
        """The texts on screen that show `row`: those that are it, else those
        that contain it as whole tokens (an ID read together with a name)."""
        texts = [el for el in self._elements
                 if el.source != "yolo" and (el.text or "").strip()]
        want = _norm_text(row)
        return ([el for el in texts if _norm_text(el.text) == want]
                or [el for el in texts if _value_in_text(row, el.text)])

    def _in_row(self, step: Step, cands: List[Candidate], anchors: List[Element]
                ) -> Tuple[List[Candidate], str]:
        """The candidates that stand in the row the step names, or why there
        are none. Exact: the text has to stand in one row only."""
        rows: List[Element] = []
        for a in anchors:
            if not any(_same_row(r, a) for r in rows):
                rows.append(a)
        if len(rows) > 1:
            return [], (f"'{step.row}' stands in {len(rows)} rows of the screen. Set row to "
                        "a text that only the row you mean shows (its ID or number)")
        by_id = {el.stable_id: el for el in self._elements}
        inside = [c for c in cands if c.key in by_id
                  and any(_same_row(a, by_id[c.key]) for a in anchors)]
        if not inside:
            return [], f"the row showing '{step.row}' has no control reading '{step.target}'"
        return inside, ""

    def _do_click(self, step: Step) -> Tuple[bool, str]:
        named = None
        if not step.element and re.fullmatch(r"\[?e\d+\]?", step.target.strip()):
            step.element, step.target = step.target, ""   # an element id written as the target
        if step.element and not step.row:
            wanted = step.element.strip().strip("[]")
            if not any(el.stable_id == wanted for el in self._elements):
                self._need_controls()   # not a text on screen: a control without text, then
            named = self.registry.get(wanted)
            if named is not None and not any(el is named for el in self._elements):
                named = None   # known id, but not on the current screen
        if step.row:
            # A row is found by its content, on the screen as it is at the
            # moment of the click: rows move when one above them is deleted or
            # the list reloads, and a position read a moment ago may belong to
            # another entry by now.
            if self._row_anchors(step.row):
                self._observe()
            else:
                self._scroll_to(lambda: bool(self._row_anchors(step.row)))
            anchors = self._row_anchors(step.row)
            if not anchors:
                return False, (f"no row showing '{step.row}' is on screen, neither further "
                               "down nor further up the page")
            cands, why = self._in_row(
                step, text_candidates(step.target, self._elements)[0], anchors)
            if why:
                return False, why
            sure = cands[0] if len(cands) == 1 and cands[0].exact else None
        else:
            cands, sure = text_candidates(step.target, self._elements, named)
            if not cands and step.target and self._scroll_to(
                    lambda: bool(text_candidates(step.target, self._elements)[0])):
                cands, sure = text_candidates(step.target, self._elements)
        if sure is None and self._foreseen[0] == step.id:
            # Planned as the click that follows the one before it, and exactly
            # one control with this text appeared with that one: a dialog's
            # button, not the equal buttons of the list beneath it.
            came = [c for c in cands if c.exact and c.key in self._foreseen[1]]
            if len(came) == 1:
                sure = came[0]
        cand = self._pick(step, cands, sure, Q_TARGET)
        if cand is not None:
            step.where = cand.row
        if cand is None and len(cands) > 1:
            return False, (f"{len(cands)} controls on screen read '{step.target}', and the "
                           "step does not say which one is meant. Set row to a text that "
                           "only its row shows (an ID), or element to the id of the one "
                           "you mean")
        if cand is None and cands:
            return False, (f"'{cands[0].text.strip()[:60]}' is on screen, but it is not "
                           f"certain that it is what '{step.target or step.element}' means")
        if cand is None:
            return False, (f"nothing matching '{step.target or step.element}' is on screen"
                           + (", neither further down nor further up the page"
                              if step.target else ""))
        kind = _click_kind(step.value)
        if kind == "double":
            self._act("double_click", {"x": cand.x, "y": cand.y})
        elif kind == "right":
            self._act("click", {"x": cand.x, "y": cand.y, "button": "right"})
        else:
            self._act("click", {"x": cand.x, "y": cand.y})
        self._settle(expect_large=True)
        change = self._observe()
        if change.is_empty:
            if not change.pixels_changed:
                return False, f"clicking '{step.target or step.element}' had no visible effect"
            # Pixels changed but no text did. A text cursor that is there now
            # says what happened: the click put the focus into a field.
            where = self._focused_label()
            if where is not None:
                return True, ("The text cursor is now in the field next to "
                              f"'{where}'." if where else "A field took the text cursor.")
            # Otherwise (a checkbox, a highlight, a toggle) only the screenshot
            # can tell whether that was the effect.
            state = self._state(step, "No text changed on screen, but its appearance did. "
                                      "The screenshot shows the screen after the click.")
            if self._ask(state, "outcome", Q_OUTCOME, ["yes", "no"], look=True) == "yes":
                return True, _TOGGLED
            return False, (f"clicking '{step.target or step.element}' changed no text on "
                           "screen, and its effect could not be confirmed")
        # The screen changed. Whether that was the right effect is not asked
        # here: a summary of what changed is too thin a basis (in the first
        # real run three of four working clicks were judged failed). The next
        # step is chosen from the new screen, and that is the check.
        if step.commits and change.transition != "replaced" and self._ask(
                self._state(step), "outcome", Q_REFUSED, ["yes", "no"], look=True,
                min_conf=self.cfg.decider_nav_conf) == "yes":
            # The one exception: a click meant to save or delete that left the
            # view in place. It fails only on evidence that the application
            # refused it (an error, a warning, a calendar that opened
            # instead). The absence of a "saved" message proves nothing: a
            # dialog asking to confirm is no such message, nor is a row that
            # vanished from a list - asked "was it saved?", the decider called
            # a deletion that had worked a failure, twice in one run.
            return False, (f"the application did not carry out the click on "
                           f"'{step.target or step.element}', as far as the screen shows "
                           f"({change.describe()}). Look at the screen before trying again")
        return True, change.describe()

    def _place(self, spec: str) -> Tuple[Optional[Tuple[int, int]], str]:
        """A place on screen, named as a point of the element list (@(x,y)),
        an element id ([e12]) or the text of an element. Returns (point, '')
        or (None, why it cannot be told). Exact: a text has to belong to one
        element only, and a point has to lie in the application's window."""
        spec = (spec or "").strip()
        if not spec:
            return None, "no place was given"
        point = None
        found = _POINT_SPEC.fullmatch(spec)
        if found:
            point = (int(found.group(1)), int(found.group(2)))
        elif re.fullmatch(r"\[?e\d+\]?", spec):
            wanted = spec.strip("[]")
            if not any(el.stable_id == wanted for el in self._elements):
                self._need_controls()   # not a text on screen: a control without text, then
            named = next((el for el in self._elements if el.stable_id == wanted), None)
            if named is None:
                return None, f"the element '{wanted}' is not on the screen as it is now"
            point = (named.center_x, named.center_y)
        else:
            cands, sure = text_candidates(spec, self._elements)
            if sure is None:
                if len(cands) > 1:
                    return None, (f"{len(cands)} elements on screen read '{spec}'. Name the "
                                  "one you mean by its id, or give the point")
                if cands:
                    return None, (f"'{cands[0].text.strip()[:60]}' is on screen, but it is "
                                  f"not certain that it is what '{spec}' means")
                return None, f"nothing reading '{spec}' is on screen"
            point = (sure.x, sure.y)
        if self._last_rect is not None:
            wx, wy, ww, wh = self._last_rect
            if not (wx <= point[0] < wx + ww and wy <= point[1] < wy + wh):
                return None, (f"the point ({point[0]},{point[1]}) lies outside the "
                              "application's window")
        return point, ""

    def _do_drag(self, step: Step) -> Tuple[bool, str]:
        """Press the mouse button on one place and let go on another. Carried
        out if the screen shows a change afterwards; whether it is the change
        that was wanted is for the next look at the screen."""
        start, why = self._place(step.element or step.target)
        if start is None:
            return False, "where to press: " + why
        end, why = self._place(step.value)
        if end is None:
            return False, "where to let go: " + why
        if start == end:
            return False, "pressing and letting go at the same place moves nothing"
        self._act("drag", {"x": start[0], "y": start[1], "to_x": end[0], "to_y": end[1]})
        self._settle(expect_large=False)
        change = self._observe()
        if change.is_empty:
            if not change.pixels_changed:
                return False, (f"dragging from ({start[0]},{start[1]}) to ({end[0]},{end[1]}) "
                               "had no visible effect")
            return True, "The screen looks different where it was dragged; no text changed."
        return True, change.describe()

    def _do_type(self, step: Step) -> Tuple[bool, str]:
        """Type a text where the text cursor is: for a field without a label
        to find it by. If the step names an element, that element is clicked
        and its content selected first. Carried out if a text that changed or
        appeared on screen shows what was typed."""
        text = step.value or step.target
        if not text.strip():
            return False, "no text was given"
        if step.element:
            wanted = step.element.strip().strip("[]")
            if not any(el.stable_id == wanted for el in self._elements):
                self._need_controls()   # not a text on screen: a control without text, then
            named = next((el for el in self._elements if el.stable_id == wanted), None)
            if named is None:
                return False, (f"the element '{wanted}' is not on the screen as it is now. "
                               "Click the field, then type")
            self._act("click", {"x": named.center_x, "y": named.center_y})
            self._act("keypress", {"keys": ["ctrl", "a"]})
        self._act("type_text", {"text": text})
        # As after a fill: what the field opens while it is typed into has to
        # be there before the screen is read, and the caret must not be read
        # as a letter after the text.
        self._settle(expect_large=False, quiet=2)
        change = self._observe(decaret=True)
        if any(_value_in_text(text, c.element.text or "")
               for c in change.text_changed + change.appeared):
            return True, f"'{text}' is on screen where it was typed"
        if not change.pixels_changed:
            return False, (f"'{text}' was typed, but the screen did not change: no field had "
                           "the text cursor. Click the field first")
        return False, (f"'{text}' was typed, but it cannot be read where the screen changed "
                       f"({change.describe()}). Look at the screen before typing again")

    def _do_simple(self, step: Step) -> Tuple[bool, str]:
        spec = step.value or step.target
        if step.op == "press":
            keys = _parse_keys(spec)
            if not keys:
                return False, "no key was given"
            # The key library ignores a name it does not know without a word,
            # and the step would count as carried out with nothing pressed.
            unknown = [k for k in keys if not pyautogui.isValidKey(_KEY_MAP.get(k, k))]
            if unknown:
                return False, (f"'{unknown[0]}' is not a key, so nothing was pressed. press "
                               "sends a key or a combination of keys (enter, tab, escape, "
                               "ctrl+s); a text is typed with fill into a field that has a "
                               "label, and with type where the text cursor is")
            self._act("keypress", {"keys": keys})
        elif step.op == "scroll":
            down = "up" not in spec.lower()
            change = self._scroll_page(down)
            if not change.pixels_changed:
                return False, (f"scrolling {'down' if down else 'up'} did not move the view: "
                               "its end is reached")
            return True, change.describe()
        else:
            try:
                seconds = max(0.0, float(spec))
            except ValueError:
                seconds = 1.5
            if _WAIT_MAX_S > 0:
                seconds = min(seconds, _WAIT_MAX_S)
            # In slices of one second, so that stop and pause do not wait for it.
            while seconds > 0:
                self._act("wait", {"seconds": min(1.0, seconds)})
                seconds -= 1.0
        return True, self._observe().describe()

    def _do_check(self, step: Step) -> Tuple[bool, str]:
        """Is a text on screen, or gone from it? Exact (whole tokens, as for a
        typed value), on the screen as it was last read; no model, no action."""
        text, want = step.target.strip(), step.value.strip().lower() or "shown"
        if not text or want not in ("shown", "gone"):
            return False, "a check needs the text in target and 'shown' or 'gone' in value"

        def shown() -> bool:
            return any(el.source != "yolo" and _value_in_text(text, el.text)
                       for el in self._elements)
        if want == "gone" and not shown() and not any(
                _value_in_text(text, seen) for seen in self._seen_texts):
            # A text that never was on screen is "gone" whatever happened.
            # (Asked of the texts read in this run, not of the registry: a row
            # that moves up into a deleted one's place takes over its id.)
            return False, (f"'{text}' was not on screen before either, so its absence "
                           "proves nothing. Check a text the entry showed (its ID)")
        if shown() != (want == "shown"):
            # The application may not have redrawn the list yet: one more look.
            self._act("wait", {"seconds": 1.0})
            self._observe()
        if shown() != (want == "shown"):
            return False, (f"'{text}' is still on screen" if want == "gone"
                           else f"'{text}' is not on screen")
        return True, (f"'{text}' is no longer on screen" if want == "gone"
                      else f"'{text}' is on screen")

    def _run_step(self, step: Step) -> Tuple[bool, str]:
        handler = {"fill": self._do_fill, "select": self._do_select,
                   "click": self._do_click, "check": self._do_check,
                   "type": self._do_type, "drag": self._do_drag,
                   }.get(step.op, self._do_simple)
        try:
            if step.op != "fill":
                self._flush_batch()   # fill decides for itself whether the batch goes on
            return handler(step)
        except Exception as exc:
            log.exception("step failed with an error")
            return False, f"error: {exc}"
