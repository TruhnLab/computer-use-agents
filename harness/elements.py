"""The elements read from a screen, and the registry that keeps their ids stable
from one look at the screen to the next and says what changed."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --- Element model + registry --------------------------------------------- #

@dataclass
class Element:
    """A single UI element detected by OCR, with a stable ID across parses."""
    stable_id: str
    text: str
    control_type: str
    source: str
    x: int
    y: int
    width: int
    height: int
    center_x: int
    center_y: int
    confidence: int = 0
    last_seen_frame: int = 0
    # OCR text that sits INSIDE a YOLO-detected control (filled edit field,
    # labeled button): the redundant YOLO box is dropped at merge time, but the
    # "this is clickable" information is fused onto the text element.
    interactive: bool = False
    # ... together with the box of that control, (x1, y1, x2, y2) on screen:
    # the text's own box says where the text is, not where the field is.
    control_rect: Optional[Tuple[int, int, int, int]] = None

    def as_prompt_line(self) -> str:
        if self.source == "yolo":
            return (
                f"[{self.stable_id}] <interactive> @({self.center_x},{self.center_y})"
                f" {self.width}x{self.height}px"
            )
        text = self.text.replace("\n", " ").strip()
        if len(text) > 60:
            text = text[:57] + "..."
        suffix = ""
        if self.interactive and self.control_rect:
            # The text and its control are one thing: say so, with the size
            # that tells a filled field from a button.
            x1, y1, x2, y2 = self.control_rect
            suffix = f" <interactive, in a control {x2 - x1}x{y2 - y1}px>"
        elif self.interactive:
            suffix = " <interactive>"
        return f"[{self.stable_id}] '{text}' @({self.center_x},{self.center_y}){suffix}"


@dataclass
class ElementChange:
    """A single per-element delta between two consecutive frames."""
    kind: str            # 'appeared' | 'disappeared' | 'moved' | 'text_changed'
    element: Element     # the CURRENT element (for disappeared, the last-seen one)
    old_text: str = ""   # text_changed only
    dx: int = 0          # moved only (new - old center)
    dy: int = 0          # moved only

    def as_line(self) -> str:
        el = self.element
        if el.source == "yolo":
            label = f"[{el.stable_id}] <interactive> @({el.center_x},{el.center_y})"
        else:
            t = (el.text or "").replace("\n", " ").strip()
            if len(t) > 40:
                t = t[:37] + "..."
            label = f"[{el.stable_id}] '{t}'"
        if self.kind == "text_changed":
            return f"{label}  ('{self.old_text}' → '{el.text}')"
        if self.kind == "moved":
            return f"{label}  (moved {self.dx:+d},{self.dy:+d})"
        return label


@dataclass
class ChangeSet:
    """
    Ground-truth summary of what changed between the previous frame and this one,
    derived from the ElementRegistry's stable-ID reconciliation. This is the
    observation the agents reason over — "what my last action caused" — rather
    than a bare snapshot of the current screen.

    `transition` names a whole-screen event when one is detected, so a scroll or
    a dialog does not flood the per-element diff:
      '' (local edits) | 'scroll' | 'popup' | 'replaced'
    """
    appeared: List[ElementChange] = field(default_factory=list)
    disappeared: List[ElementChange] = field(default_factory=list)
    moved: List[ElementChange] = field(default_factory=list)
    text_changed: List[ElementChange] = field(default_factory=list)
    transition: str = ""
    scroll_dy: int = 0        # scroll only: median vertical shift (+ = content moved down)
    # Screen-absolute position of a blinking text caret, detected as a byproduct
    # of decaret (the caret is REMOVED from the screenshot, so this is the ONLY
    # signal that a click landed in an edit field — clicking into a field often
    # causes no other visible change).
    caret_xy: Optional[Tuple[int, int]] = None
    # Geometric resolution of WHICH field the caret sits in, e.g.
    # "inside [e81] 'John', next to label 'First Name *'" (may be empty).
    caret_context: str = ""
    # The frame differs from the previous one at pixel level. Elements can stay
    # the same while pixels change (a checkbox ticked, a row highlighted), so
    # an empty change-set with this flag set means "changed, but not in text".
    pixels_changed: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.appeared or self.disappeared or self.moved or self.text_changed)

    def describe(self) -> str:
        """What the screen did, in one plain sentence for the person watching
        (the counts of summary() mean nothing to a clinician). Where a view or
        dialog opened, it is named by its most prominent new text: the tallest."""
        def texts(changes: List[ElementChange]) -> List[Element]:
            return [c.element for c in changes
                    if c.element.source != "yolo" and (c.element.text or "").strip()]

        def quoted(els: List[Element], limit: int = 3) -> str:
            shown = ", ".join(f"'{el.text.strip()[:40]}'" for el in els[:limit])
            return shown + (" and more" if len(els) > limit else "")

        new = texts(self.appeared)
        heading = max(new, key=lambda el: el.height).text.strip()[:60] if new else ""
        if self.transition == "replaced":
            return (f"A different view opened, showing '{heading}'." if heading
                    else "A different view opened.")
        if self.transition == "popup":
            return f"A dialog opened: '{heading}'." if heading else "A dialog opened."
        if self.transition == "scroll":
            return ("The view moved" + (f"; now visible: {quoted(new)}." if new else "."))
        if new:
            return f"New on screen: {quoted(new)}."
        if self.text_changed:
            c = self.text_changed[0]
            return f"'{c.old_text[:30]}' changed to '{c.element.text.strip()[:30]}'."
        if self.disappeared:
            return "Part of the screen closed."
        if self.moved:
            return "The layout shifted."
        return "The screen did not change."

    def summary(self) -> str:
        """Compact single-line summary — safe for the (newline-delimited) log/SSE stream."""
        s = self._summary_base()
        if self.caret_xy:
            where = f" {self.caret_context}" if self.caret_context else ""
            s += f"; caret blinking at ({self.caret_xy[0]},{self.caret_xy[1]}){where} — field focused"
        return s

    def _summary_base(self) -> str:
        if self.transition == "replaced":
            return f"screen replaced ({len(self.disappeared)} gone, {len(self.appeared)} new)"
        if self.transition == "scroll":
            d = "down" if self.scroll_dy < 0 else "up"
            return f"scrolled {d} ~{abs(self.scroll_dy)}px ({len(self.appeared)} newly visible)"
        if self.is_empty:
            return "no visible change"
        parts = []
        if self.text_changed:
            parts.append(f"{len(self.text_changed)} changed")
        if self.appeared:
            parts.append(f"{len(self.appeared)} appeared")
        if self.disappeared:
            parts.append(f"{len(self.disappeared)} disappeared")
        if self.moved:
            parts.append(f"{len(self.moved)} moved")
        prefix = "popup + " if self.transition == "popup" else ""
        return prefix + ", ".join(parts)


class ElementRegistry:
    """Assigns short stable IDs to OCR elements across parses."""

    # A matched element must shift at least this many px (either axis) to count
    # as "moved" — filters OCR jitter and sub-pixel box wobble.
    _MOVE_EPS = 8

    def __init__(self, match_threshold: float = 0.62, evict_after_frames: int = 20):
        self._by_id: Dict[str, Element] = {}
        self._next_id = 1
        self._frame = 0
        self._match_threshold = match_threshold
        self._evict_after = evict_after_frames

    def _mint_id(self) -> str:
        sid = f"e{self._next_id}"
        self._next_id += 1
        return sid

    @staticmethod
    def _similarity(new: Element, old: Element, screen_w: int, screen_h: int) -> float:
        if new.text and old.text:
            text_sim = difflib.SequenceMatcher(None, new.text.lower(), old.text.lower()).ratio()
        else:
            text_sim = 0.4 if (not new.text and not old.text) else 0.0
        type_sim = 1.0 if new.control_type == old.control_type else 0.3
        source_sim = 1.0 if new.source == old.source else 0.5
        dx = abs(new.center_x - old.center_x) / max(1, screen_w)
        dy = abs(new.center_y - old.center_y) / max(1, screen_h)
        pos_sim = max(0.0, 1.0 - 4.0 * (dx + dy))
        dw = abs(new.width - old.width) / max(1, screen_w)
        dh = abs(new.height - old.height) / max(1, screen_h)
        size_sim = max(0.0, 1.0 - 4.0 * (dw + dh))
        return (
            0.45 * text_sim + 0.15 * type_sim + 0.10 * source_sim
            + 0.20 * pos_sim + 0.10 * size_sim
        )

    def reconcile(
        self, new_elements: List[Element], screen_w: int, screen_h: int
    ) -> Tuple[List[Element], ChangeSet]:
        """Assign stable IDs and, as a side product, compute the frame-to-frame
        ChangeSet (appeared / disappeared / moved / text-changed + transition)."""
        prev_frame = self._frame
        self._frame += 1
        # Shallow copy so old Element objects survive the in-place id reassignments
        # below (we replace dict values, we don't mutate the old objects).
        prev_snapshot = dict(self._by_id)
        prev_visible_ids = {
            rid for rid, el in prev_snapshot.items() if el.last_seen_frame == prev_frame
        }

        # Position gate: a real element never teleports horizontally, and moves
        # vertically only within roughly a screen (scroll). Reject candidate
        # matches beyond these bounds even when text is similar — this stops a
        # button ('Speichern') from being relabeled as a far-away status
        # ('Gespeichert'), which both pollutes the change-set and corrupts the
        # stable IDs the executor clicks by.
        max_dx = 0.30 * screen_w
        max_dy = 0.85 * screen_h

        unmatched_ids = set(self._by_id.keys())
        result: List[Element] = []
        change = ChangeSet()
        matched_ids: set = set()
        for elem in new_elements:
            best_id, best_score = None, self._match_threshold
            if elem.stable_id in unmatched_ids and self._by_id[elem.stable_id] is elem:
                # The very object from the last frame (a partial parse keeps
                # every element outside the changed regions): same id, and
                # nothing to compare.
                best_id = elem.stable_id
            for rid in unmatched_ids if best_id is None else ():
                old = self._by_id[rid]
                if abs(elem.center_x - old.center_x) > max_dx:
                    continue
                if abs(elem.center_y - old.center_y) > max_dy:
                    continue
                score = self._similarity(elem, old, screen_w, screen_h)
                if score > best_score:
                    best_score = score
                    best_id = rid
            if best_id is not None:
                old = prev_snapshot[best_id]
                elem.stable_id = best_id
                elem.last_seen_frame = self._frame
                self._by_id[best_id] = elem
                unmatched_ids.remove(best_id)
                matched_ids.add(best_id)
                # Only diff against elements that were actually visible last frame.
                if best_id in prev_visible_ids:
                    if elem.text.strip() != old.text.strip():
                        change.text_changed.append(
                            ElementChange("text_changed", elem, old_text=old.text.strip())
                        )
                    dx = elem.center_x - old.center_x
                    dy = elem.center_y - old.center_y
                    if abs(dx) >= self._MOVE_EPS or abs(dy) >= self._MOVE_EPS:
                        change.moved.append(ElementChange("moved", elem, dx=dx, dy=dy))
                elif prev_frame > 0:
                    # The id is one from a few frames ago, but the frame before
                    # this one did not show it: on the screen it is new.
                    change.appeared.append(ElementChange("appeared", elem))
            else:
                elem.stable_id = self._mint_id()
                elem.last_seen_frame = self._frame
                self._by_id[elem.stable_id] = elem
                # Genuinely new only if there was a prior frame to be new against.
                if prev_frame > 0:
                    change.appeared.append(ElementChange("appeared", elem))
            result.append(elem)

        # Disappeared = visible last frame, not matched this frame.
        for rid in prev_visible_ids - matched_ids:
            change.disappeared.append(ElementChange("disappeared", prev_snapshot[rid]))

        # The similarity matcher is text-weighted, so an in-place value replacement
        # (a field going 'Speichern' → '444444', or an empty YOLO field → typed text)
        # falls below threshold and lands as a disappeared+appeared pair at the same
        # box. Re-pair those into text_changed — it's the #1 form-filling signal.
        self._pair_inplace_text_changes(change)

        self._classify_transition(change, len(prev_visible_ids))

        stale = [rid for rid, el in self._by_id.items()
                 if self._frame - el.last_seen_frame > self._evict_after]
        for rid in stale:
            del self._by_id[rid]
        return result, change

    @staticmethod
    def _box_iou(a: Element, b: Element) -> float:
        ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
        ix2 = min(a.x + a.width, b.x + b.width)
        iy2 = min(a.y + a.height, b.y + b.height)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        union = a.width * a.height + b.width * b.height - inter
        return inter / union if union > 0 else 0.0

    def _pair_inplace_text_changes(self, change: ChangeSet, iou_thresh: float = 0.3) -> None:
        """Collapse co-located disappeared+appeared pairs into text_changed."""
        if not change.appeared or not change.disappeared:
            return
        used: set = set()
        kept_appeared: List[ElementChange] = []
        for app in change.appeared:
            best_i, best_iou = None, iou_thresh
            for i, dis in enumerate(change.disappeared):
                if i in used:
                    continue
                iou = self._box_iou(app.element, dis.element)
                if iou > best_iou:
                    best_iou, best_i = iou, i
            if best_i is None:
                kept_appeared.append(app)
            else:
                used.add(best_i)
                old_text = change.disappeared[best_i].element.text.strip()
                change.text_changed.append(
                    ElementChange("text_changed", app.element, old_text=old_text)
                )
        change.appeared = kept_appeared
        change.disappeared = [d for i, d in enumerate(change.disappeared) if i not in used]

    @staticmethod
    def _classify_transition(change: ChangeSet, prev_n: int) -> None:
        """Detect a whole-screen event (scroll / popup / replaced) from the raw
        deltas, so downstream renderers can collapse the noise into one fact."""
        if prev_n == 0:
            return  # first frame — everything is 'appeared', not a transition
        gone, new = len(change.disappeared), len(change.appeared)

        # Screen replaced: most of the previous screen gone, lots of new content.
        if prev_n >= 5 and gone >= 0.7 * prev_n and new >= max(3, 0.4 * prev_n):
            change.transition = "replaced"
            return

        # Scroll: a coherent block of elements shifted by a common vertical delta.
        if len(change.moved) >= 3:
            dys = sorted(c.dy for c in change.moved)
            median = dys[len(dys) // 2]
            if abs(median) >= 20:
                coherent = sum(1 for c in change.moved if abs(c.dy - median) <= 15)
                if coherent >= 3 and coherent >= 0.6 * len(change.moved):
                    change.transition = "scroll"
                    change.scroll_dy = median
                    return

        # Popup/dialog: prior screen mostly intact, a cluster of new elements arrived.
        if new >= 3 and gone <= 2 and prev_n >= 3 and (prev_n - gone) >= 0.6 * prev_n:
            change.transition = "popup"

    def get(self, stable_id: str) -> Optional[Element]:
        return self._by_id.get(stable_id)
