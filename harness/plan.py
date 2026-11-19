"""The plan: its steps, and reading a plan out of the planner's reply."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .prompts import _STEP_OPS

log = logging.getLogger("agent")


# --- Plan (the steps the planner wrote, executed one by one) ---------------- #


def _click_kind(value: str) -> str:
    """Which click a click step asks for: 'double', 'right' or '' (ordinary)."""
    kind = (value or "").strip().lower()
    return "double" if kind.startswith("double") else "right" if kind.startswith("right") else ""


# A point of the screen as the element list writes it: @(x,y).
_POINT_SPEC = re.compile(r"@?\(?\s*(-?\d+)\s*,\s*(-?\d+)\s*\)?")


@dataclass
class Step:
    """One planned action. `target` is what is visible on screen (a field label
    or a control's text), never a coordinate: code and the decider resolve it
    against the current element list at the moment the step runs."""
    id: int
    op: str                    # one of _STEP_OPS
    target: str = ""
    value: str = ""
    element: str = ""          # element id named by the planner (click only, optional)
    row: str = ""              # a text of the row the control stands in (click only, optional)
    where: str = ""            # the texts of the row a click went to, as read on screen
    status: str = "pending"    # pending | in_progress | done | failed | skipped
    note: str = ""             # what was seen, or why it failed: a sentence for the operator
    detail: str = ""           # the change counts behind the note, for debugging
    commits: bool = False      # saves, sends, signs, orders or deletes something
    planned_by: str = "planner"   # 'planner' | 'decider' (a navigation click it chose)
    # Who settled the step once it has ended: 'code' (no model asked), 'decider'
    # (an accepted decider answer was used) or 'planner' (handed back to it).
    resolved_by: Optional[str] = None
    seconds: Optional[float] = None   # how long the step took
    checked: bool = True       # False while a typed value has not been read back

    @property
    def text(self) -> str:
        if self.op == "fill":
            return f"Fill '{self.target}' with '{self.value}'"
        if self.op == "select":
            return f"Select '{self.value}' in '{self.target}'"
        if self.op == "type":
            return f"Type '{self.value or self.target}'"
        if self.op == "click":
            kind = _click_kind(self.value)
            return (("Double-click" if kind == "double" else "Right-click" if kind == "right"
                     else "Click") + f" '{self.target or self.element}'"
                    + (f" in the row with '{self.row}'" if self.row else ""))
        if self.op == "drag":
            return f"Drag '{self.target or self.element}' to '{self.value}'"
        if self.op == "check":
            return (f"Check that '{self.target}' is "
                    + ("gone" if self.value.strip().lower() == "gone" else "shown"))
        if self.op == "press":
            return f"Press {self.value or self.target}"
        if self.op == "scroll":
            return f"Scroll {self.value or self.target or 'down'}"
        return f"Wait {self.value or '1.5'}s"

    # The value/label pair the on-screen checks are anchored to
    # (check_value_near_label, guard_wrong_field).
    @property
    def expected_value(self) -> str:
        return self.value if self.op in ("fill", "select") else ""

    @property
    def expected_label(self) -> str:
        return self.target if self.op in ("fill", "select") else ""


class Plan:
    """Ordered steps with stable ids, executed front to back."""

    def __init__(self) -> None:
        self._steps: List[Step] = []
        self._next_id = 1
        # What failed and was set aside so that the rest could go on: named
        # to the user at the end, each as a sentence.
        self.open: List[str] = []

    def steps(self) -> List[Step]:
        return list(self._steps)

    def add(self, op: str, target: str = "", value: str = "", element: str = "",
            commits: bool = False, row: str = "") -> Step:
        step = Step(id=self._next_id, op=op, target=target.strip(),
                    value=value.strip(), element=element.strip(), commits=commits,
                    row=row.strip())
        self._next_id += 1
        self._steps.append(step)
        return step

    def pending(self) -> List[Step]:
        return [s for s in self._steps if s.status == "pending"]

    def next_pending(self) -> Optional[Step]:
        return next((s for s in self._steps if s.status == "pending"), None)

    def drop_pending(self, note: str) -> List[int]:
        """Retire the steps not started yet: the planner writes new ones from
        the current screen. Returns the ids of the dropped steps."""
        dropped = self.pending()
        for s in dropped:
            s.status = "skipped"
            s.note = note
            log.info("[TODO] − %s (replanned)", s.text)
        return [s.id for s in dropped]

    def take_pending(self) -> List[Step]:
        """Take the steps not started yet out of the plan, so that others can
        be added in front of them. put_back returns them."""
        taken = self.pending()
        self._steps = [s for s in self._steps if s.status != "pending"]
        return taken

    def remove(self, steps: List[Step]) -> None:
        """Take steps out of the plan without a trace: they are about to be
        added again as they were."""
        self._steps = [s for s in self._steps if not any(s is r for r in steps)]

    def put_back(self, steps: List[Step]) -> None:
        """Steps taken out go behind those added since, under new ids: the
        ids stay in the order of the list."""
        for s in steps:
            s.id = self._next_id
            self._next_id += 1
            self._steps.append(s)

    def render_open(self) -> str:
        return "\n".join(f"  - {point}" for point in self.open)

    def render_done(self) -> str:
        """What happened so far, for the planner and the completion check."""
        lines = []
        for s in self._steps:
            # Which of several equal controls it was: without the row, "Click
            # 'Delete'" eight times over tells nobody what was deleted.
            where = f" [the row: {s.where}]" if s.where else ""
            if s.status == "done":
                lines.append(f"  ✓ {s.text}{where}" + (f" ({s.note})" if s.note else ""))
            elif s.status == "failed":
                lines.append(f"  ✗ {s.text}{where} — FAILED: {s.note}")
        return "\n".join(lines)

    def to_marker(self) -> str:
        """Compact single-line JSON for the [TODOS] log marker (drives the UI)."""
        return json.dumps(
            [{"id": s.id, "text": s.text, "status": s.status, "corrective": False,
              "op": s.op, "note": s.note, "detail": s.detail, "commits": s.commits,
              "planned_by": s.planned_by,
              "resolved_by": s.resolved_by, "seconds": s.seconds, "checked": s.checked,
              "row": s.where}
             for s in self._steps],
            ensure_ascii=False, separators=(",", ":"),
        )


@dataclass
class Decision:
    """The decider's answer to one choice question."""
    choice: Optional[str] = None
    confidence: Optional[float] = None   # None: the backend reports none (chat model)


def _parse_keys(spec: str) -> List[str]:
    """'ctrl+s' → ['ctrl', 's']; 'Enter' → ['enter']."""
    return [k.strip().lower() for k in re.split(r"[+\s]+", spec or "") if k.strip()]


def _parse_json_object(content: str) -> Optional[Dict[str, Any]]:
    """The JSON object in a model reply, tolerating text or a code fence around it."""
    content = (content or "").strip()
    for candidate in (content, content[content.find("{"):content.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


_LISTING_NOTATION = re.compile(
    r"\[e\d+\]"                       # the element's id
    r"|@\(\s*-?\d+\s*,\s*-?\d+\s*\)"   # its position
    r"|<interactive[^>]*>"
    r"|\b\d+x\d+px\b")                # a control's size


def _strip_listing(text: str) -> str:
    """An evidence text without the notation of the element list. The planner
    reads elements as "[e12] 'Address' @(928,855)" and often quotes them back
    that way; the id and the position are ours, not text on the screen."""
    text = _LISTING_NOTATION.sub(" ", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return " ".join(text.split())


def parse_plan(content: str) -> Optional[Dict[str, Any]]:
    """A planner reply as a plan dict with every field present, or None if it
    is not a JSON object. Steps with an unknown op are dropped, not guessed."""
    obj = _parse_json_object(content)
    if obj is None:
        return None
    steps = []
    for raw in obj.get("steps") or []:
        if not isinstance(raw, dict) or raw.get("op") not in _STEP_OPS:
            log.warning("planner step ignored (unknown op): %s", raw)
            continue
        step = {**{k: str(raw.get(k) or "")
                   for k in ("op", "target", "value", "element", "row")},
                "commits": raw.get("commits") is True}
        if step["op"] == "fill" and re.fullmatch(r"\[?e\d+\]?", step["target"].strip()):
            step["element"], step["target"] = step["target"].strip(), ""   # an id as the target
        if step["op"] == "fill" and not step["target"].strip():
            # No label to find a field by: the text goes where the cursor is,
            # or into the element the step names.
            step["op"] = "type"
        steps.append(step)
    evidence = obj.get("evidence") or []
    options = obj.get("options") or []
    return {
        "assessment": str(obj.get("assessment") or ""),
        "steps": steps,
        "final": bool(obj.get("final")),
        "answer": str(obj.get("answer") or ""),
        "evidence": [t for t in (_strip_listing(str(e)) for e in
                                 (evidence if isinstance(evidence, list) else [evidence])) if t],
        "question": str(obj.get("question") or ""),
        "options": [str(o) for o in options] if isinstance(options, list) else [],
        "keep_planned": obj.get("keep_planned") is True,
        "set_aside": " ".join(str(obj.get("set_aside") or "").split()),
    }
