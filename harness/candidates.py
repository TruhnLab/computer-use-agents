"""The elements a step can mean: the candidates for a field and for a text."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .elements import Element
from .matching import (
    _find_label_elements,
    _fuzzy_text_match,
    _is_value_adjacent_to_label,
    _label_in_text,
    _nearest_label,
    _norm_text,
    _same_row,
)


@dataclass
class Candidate:
    """A place a step could act on, offered to the decider under `key`."""
    key: str             # the element id
    x: int               # where to click, screen-absolute
    y: int
    desc: str            # one line for the decider
    text: str = ""       # text the element shows now
    tier: int = 1        # 1 control next to the label | 2 text there | 3 the label itself
    exact: bool = False  # target text matched exactly (not fuzzily)
    row: str = ""        # the other texts in its row, if it is one of several candidates

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


_MAX_CANDIDATES = 12   # options per target question


_MAX_OPTIONS = 25      # options per opened-list question


def _boxes_overlap(a: Element, b: Element) -> bool:
    """Do the boxes of two elements share any area?"""
    return (a.x < b.x + b.width and b.x < a.x + a.width
            and a.y < b.y + b.height and b.y < a.y + a.height)


def _label_distance(el: Element, label_el: Element) -> int:
    return abs(el.center_y - label_el.center_y) + max(0, el.x - (label_el.x + label_el.width))


def _owner_label(el: Element, elements: List[Element]) -> Optional[Element]:
    """The label a control most plausibly belongs to: the nearest text left of
    it or above it that is not itself the content of a control."""
    return _nearest_label(el, [e for e in elements if not e.interactive])


def _describe(el: Element, elements: List[Element], label_el: Optional[Element] = None) -> str:
    """One line telling the decider what an element is and where it sits."""
    text = (el.text or "").replace("\n", " ").strip()
    if el is label_el:
        return f"the label '{text[:50]}' itself at ({el.center_x},{el.center_y})"
    if el.source == "yolo":
        what = f"control without text, {el.width}x{el.height}px"
    else:
        what = f"'{text[:50]}'" + (" (inside a control)" if el.interactive else "")
    if label_el is None:
        near = _nearest_label(el, elements)
        where = f", next to the label '{near.text.strip()[:40]}'" if near is not None else ""
        return f"{what} at ({el.center_x},{el.center_y}){where}"
    # A candidate for a label: say on which side of the label it sits, and
    # name the label it is closer to if that is another one — in a form with
    # two columns the field of the neighbouring column is also "next to" it.
    side = "below" if el.y >= label_el.y + label_el.height - 4 else "to the right of"
    where = f", {side} the label '{label_el.text.strip()[:40]}'"
    owner = _owner_label(el, elements)
    if owner is not None and owner is not label_el:
        where += f", but closer to the label '{owner.text.strip()[:40]}'"
    return f"{what} at ({el.center_x},{el.center_y}){where}"


def field_candidates(
    label: str, elements: List[Element]
) -> Tuple[List[Candidate], Optional[Candidate]]:
    """Where could the field labelled `label` be clicked?

    Returns (candidates, sure). Candidates come from label-anchored geometry:
    controls next to a matching label first, then text sitting there (a value
    the detector saw no control around), then the label itself (clicking a
    label focuses its field in many UIs). `sure` is set only when exactly one
    label matches EXACTLY and exactly one control sits next to it — anything
    fuzzier is a question for the decider, never a silent pick."""
    want = _norm_text(label)
    labels = _find_label_elements(label, elements)
    exact = [el for el in labels if _norm_text(el.text) == want]
    cands: List[Candidate] = []
    seen: set = set()

    def add(el: Element, tier: int, label_el: Optional[Element], x: Optional[int] = None) -> None:
        if id(el) in seen:
            return
        seen.add(id(el))
        cands.append(Candidate(
            key=el.stable_id, x=x if x is not None else el.center_x, y=el.center_y,
            desc=_describe(el, elements, label_el),
            text="" if el.source == "yolo" else (el.text or ""),
            tier=tier, exact=label_el is not None and label_el in exact,
        ))

    for l_el in exact or labels:
        adjacent = sorted(
            (el for el in elements
             if el is not l_el and _is_value_adjacent_to_label(el, l_el)),
            key=lambda el: _label_distance(el, l_el),
        )
        controls = [el for el in adjacent if el.source == "yolo" or el.interactive]
        # A control next to this label AND closer to another label is that
        # label's field (two columns: the neighbour's field sits on this
        # field's row). It stays a candidate, but never as the label's own:
        # with no own control detected, the label is clicked and the text
        # cursor has to prove the field.
        own = [el for el in controls if _owner_label(el, elements) in (None, l_el)]
        if len(own) > 1:
            # Two boxes for one field: the detector sometimes draws one across
            # several controls. A box lying over the field of another label is
            # not this label's field. Only to settle such a tie: a single own
            # control is never questioned, and if none would be left, all stay.
            others = [el for el in elements
                      if (el.source == "yolo" or el.interactive) and el not in own
                      and _owner_label(el, elements) not in (None, l_el)]
            clear = [el for el in own if not any(_boxes_overlap(el, o) for o in others)]
            if clear:
                own = clear
        for el in own:
            add(el, 1, l_el)
        for el in controls:
            add(el, 2, l_el)
        for el in adjacent:
            if el.source != "yolo" and not el.interactive and el not in labels:
                add(el, 2, l_el)
    # OCR often merges label and value into ONE box ('Nachname Doe'): the field
    # is the right part of that box.
    for el in elements:
        if (el.source != "yolo" and el not in labels
                and _label_in_text(label, el.text) and _norm_text(el.text) != want):
            add(el, 1, None, x=el.x + int(0.75 * el.width))
    for l_el in exact or labels:
        add(l_el, 3, l_el)

    cands.sort(key=lambda c: c.tier)
    controls = [c for c in cands if c.tier == 1]
    sure = controls[0] if len(exact) == 1 and len(controls) == 1 and controls[0].exact else None
    return cands, sure


def text_candidates(
    target: str, elements: List[Element], named: Optional[Element] = None
) -> Tuple[List[Candidate], Optional[Candidate]]:
    """Which element is the control showing `target`?

    Returns (candidates, sure): exact text matches, else fuzzy ones, with the
    element the planner named (if any) first. `sure` is the element the
    planner named, or else a single exact match."""
    want = _norm_text(target)
    texts = [el for el in elements if el.source != "yolo" and (el.text or "").strip()]
    exact = [el for el in texts if want and _norm_text(el.text) == want]
    pool = exact
    if not pool and want:
        pool = sorted(
            (el for el in texts if _fuzzy_text_match(target, el.text, 0.8)),
            key=lambda el: -difflib.SequenceMatcher(None, want, _norm_text(el.text)).ratio(),
        )
    ordered = ([named] if named is not None else []) + [el for el in pool if el is not named]

    def click_x(el: Element) -> int:
        # OCR often merges neighbours into ONE box (a heading and the link
        # next to it): the control is the part of the box that shows the target.
        text, part = (el.text or "").lower(), target.strip().lower()
        at = text.find(part) if part and el not in exact else -1
        if at < 0 or not text:
            return el.center_x
        return el.x + int(el.width * (at + len(part) / 2) / len(text))

    def row_of(el: Element) -> str:
        # Several controls with one text (a button in every row of a list)
        # differ only by what stands next to them.
        row = sorted((o for o in texts if o is not el and o not in ordered
                      and _same_row(o, el)), key=lambda o: o.x)
        return " | ".join(o.text.strip() for o in row)[:100]

    rows = {id(el): row_of(el) if len(ordered) > 1 else "" for el in ordered}
    cands = [
        Candidate(key=el.stable_id, x=click_x(el), y=el.center_y,
                  desc=_describe(el, elements) + (
                      f"; in the row: {rows[id(el)]}" if rows[id(el)] else ""),
                  text="" if el.source == "yolo" else (el.text or ""),
                  exact=el in exact or el is named, row=rows[id(el)])
        for el in ordered
    ]
    # Certain is: the element the planner named, if it shows the target's
    # text (or has none of its own); else a single exact match.
    if named is not None and (not want or named.source == "yolo"
                              or _norm_text(named.text) == want):
        sure = cands[0]
    else:
        sure = cands[0] if len(cands) == 1 and cands[0].exact else None
    return cands, sure
