"""Reading the screen during a run, as little of it as the change allows."""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .elements import ChangeSet, Element, ElementRegistry
from .matching import _same_row, describe_caret_position
from .paths import _debug_screenshot_path, _raw_screenshot_path
from .perception import (
    _YOLO_IMGSZ,
    _YOLO_TEXT_FUSE_THRESH,
    _coverage,
    extract_icon_elements,
    extract_ocr_elements,
)
from .screen import (
    _PARTIAL_MAX_FRAC,
    _PARTIAL_PAD,
    _below_title_bar,
    _changed_boxes,
    _decaret,
    _diff_regions,
    _merge_rects,
    annotate_screenshot,
    capture_foreground,
    capture_foreground_burst,
)

log = logging.getLogger("agent")


class ScreenReading:
    """The part of ComputerAgent that reads the screen."""

    def _parse_screen(self, decaret: bool = False) -> Tuple[str, List[Element], ChangeSet]:
        """
        Capture foreground window, read it, reconcile IDs, ANNOTATE the image
        (boxes + IDs + crosshair at last-click), return (base64 of annotated
        image, elements, change-set-vs-previous-frame).

        Reading costs as little as the change allows: an identical frame reuses
        the cached elements, a locally changed frame re-reads only the changed
        regions (_reread_changed), anything else gets a full text read. Controls
        without text are detected on demand (_need_controls).

        When *decaret* is True, capture a short burst of frames and remove the
        blinking text caret before OCR (see _decaret) — used after actions that
        focus/edit a field, where the caret corrupts field text OCR.
        """
        t0 = time.monotonic()
        caret_centers: List[Tuple[int, int]] = []
        if decaret:
            frames, (win_x, win_y, win_w, win_h) = capture_foreground_burst()
            screenshot, caret_centers = _decaret(frames)
        else:
            screenshot, (win_x, win_y, win_w, win_h) = capture_foreground()
        log.info("screenshot: %.2fs (window %dx%d at %d,%d)%s",
                 time.monotonic() - t0, win_w, win_h, win_x, win_y,
                 " [decaret]" if decaret else "")

        # Both fast paths need the previous frame of the SAME window rect:
        # cached coords are screen-absolute.
        rect = (win_x, win_y, win_w, win_h)
        merged: Optional[List[Element]] = None
        pixels_changed = False
        if (self._prev_screenshot is not None and self._last_elements is not None
                and rect == self._last_rect):
            # Fast path 1: BIT-IDENTICAL (RGB-exact) to the previous frame →
            # OCR/YOLO are deterministic, so their output would be identical
            # too; reuse the cached elements and skip the inference. Exact
            # equality, deliberately: any tolerance can swallow a real hint (a
            # single small glyph is <24px; a red→green indicator of equal
            # luminance is invisible to a grayscale diff). Static-window
            # capture is deterministic (composited framebuffer), so exactness
            # still fires in practice.
            a = np.asarray(self._prev_screenshot.convert("RGB"), dtype=np.uint8)
            b = np.asarray(screenshot.convert("RGB"), dtype=np.uint8)
            if a.shape == b.shape and np.array_equal(a, b):
                log.info("screen unchanged — OCR/YOLO skipped, cached elements reused")
                self._parses["cached"] += 1
                self._prev_screenshot = screenshot
                change = ChangeSet()
                if caret_centers:
                    change.caret_xy = (caret_centers[0][0] + win_x,
                                       caret_centers[0][1] + win_y)
                    change.caret_context = describe_caret_position(
                        change.caret_xy, self._last_elements)
                annotated = annotate_screenshot(
                    screenshot, self._last_elements, win_x, win_y,
                    click_marker=self.executor.last_click_point,
                )
                if self.cfg.save_debug_screenshots:
                    self._save_debug(annotated, screenshot, rect)
                buf = BytesIO()
                annotated.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode(), self._last_elements, change
            pixels_changed = True
            # Fast path 2: only the changed regions are read again.
            t1 = time.monotonic()
            merged = self._reread_changed(screenshot, win_x, win_y)
            if merged is not None:
                self._parses["partial"] += 1
                log.info("partial parse: %.2fs", time.monotonic() - t1)

        text_only_full = merged is None
        prev_texts = sum(1 for el in (self._last_elements or []) if el.source != "yolo")
        if merged is None:
            t1 = time.monotonic()
            merged = extract_ocr_elements(screenshot)
            self._parses["full"] += 1
            # The texts are read anew. The controls of the frame before are
            # carried over where the picture allows it (same window, and they
            # were detected for that frame).
            carried = (self._carry_controls(screenshot, win_x, win_y)
                       if (self._controls_ready and not self.cfg.controls_always
                           and self._prev_screenshot is not None
                           and self._last_elements is not None and rect == self._last_rect)
                       else None)
            self._controls_ready = False
            log.info("OCR: %.2fs \u2192 %d text elements", time.monotonic() - t1, len(merged))
            icons: List[Element] = carried or []
            if self.cfg.controls_always:
                t2 = time.monotonic()
                icons = extract_icon_elements(screenshot)
                log.info("controls: %.2fs \u2192 %d detected", time.monotonic() - t2, len(icons))
            # Offset coords from window-relative to screen-absolute.
            for el in merged + icons:
                el.x += win_x
                el.y += win_y
                el.center_x += win_x
                el.center_y += win_y
            if self.cfg.controls_always:
                # The whole screen with every full read: the planner always
                # sees the controls without text, and one that appears or
                # vanishes is part of what changed.
                merged = self._merge_text_and_icons(merged, _below_title_bar(icons))
                self._controls_ready = True
                self._parses["controls"] += 1
                text_only_full = False
            elif carried is not None:
                merged = self._merge_text_and_icons(merged, _below_title_bar(icons))
                self._controls_ready = True
                text_only_full = False

        merged = _below_title_bar(merged)   # the window's own close button is not a target
        reconciled, change = self.registry.reconcile(merged, self.width, self.height)
        if text_only_full:
            # Controls detected on the previous frame are not "gone": they were
            # simply not looked for this time. Judge the transition on text.
            change.disappeared = [c for c in change.disappeared if c.element.source != "yolo"]
            change.transition, change.scroll_dy = "", 0
            ElementRegistry._classify_transition(change, prev_texts)
        change.pixels_changed = pixels_changed
        self._last_elements = reconciled     # cache for the fast paths
        self._last_rect = rect
        if caret_centers:
            # Surface the (healed-away) caret as a focus indicator, screen-absolute,
            # resolved to the field it sits in.
            change.caret_xy = (caret_centers[0][0] + win_x, caret_centers[0][1] + win_y)
            change.caret_context = describe_caret_position(change.caret_xy, reconciled)

        # Pixel-diff highlight: mark what changed since the previous frame — but
        # skip on scroll/replaced, where "everything" changed and a wash is noise.
        highlight_regions: List[Tuple[int, int, int, int]] = []
        if (self._prev_screenshot is not None
                and change.transition not in ("scroll", "replaced")):
            try:
                highlight_regions = _diff_regions(self._prev_screenshot, screenshot)
            except Exception as exc:
                log.debug("pixel-diff highlight failed: %s", exc)
        self._prev_screenshot = screenshot  # raw frame for the next diff

        # Annotate — same image for the models and for the debug screenshot file.
        annotated = annotate_screenshot(
            screenshot, reconciled, win_x, win_y,
            click_marker=self.executor.last_click_point,
            highlight_regions=highlight_regions,
        )

        if self.cfg.save_debug_screenshots:
            self._save_debug(annotated, screenshot, rect)

        buf = BytesIO()
        annotated.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        return b64, reconciled, change

    @staticmethod
    def _merge_text_and_icons(
        ocr_elements: List[Element], icon_elements: List[Element]
    ) -> List[Element]:
        """Keep all OCR elements; add the YOLO icon elements that have no text
        inside them. A text counts as inside when YOLO_TEXT_FUSE_THRESH of ITS
        area lies within the box (coverage relative to the text, not IoU: a
        small text in a large field box has a tiny IoU). That text then stands
        for the control; a text that only grazes the box changes nothing.

        A box whose texts stand in different rows is not one control but a
        panel (a block of menu entries, a part of a form, often a weak
        detection): it marks none of them. Such boxes marked field labels as
        "inside a control", and a label marked that way is no label any more
        for the field lookup."""
        ocr_boxes = [
            {"x": el.x, "y": el.y, "w": el.width, "h": el.height}
            for el in ocr_elements
        ]
        merged: List[Element] = list(ocr_elements)
        for icon_el in icon_elements:
            icon_box = {"x": icon_el.x, "y": icon_el.y,
                        "w": icon_el.width, "h": icon_el.height}
            coverages = [_coverage(icon_box, ob) for ob in ocr_boxes]
            inside = [el for el, cov in zip(ocr_elements, coverages)
                      if cov >= _YOLO_TEXT_FUSE_THRESH]
            if inside and not all(_same_row(inside[0], el) for el in inside[1:]):
                continue   # a panel around several rows of text: no control of its own
            if inside:
                # Redundant blue overlay — drop the YOLO box, but FUSE its
                # meaning onto the OCR text that sits mostly inside it: that
                # text IS a clickable control (filled edit field, labeled
                # button). Without this the "interactive" information would be
                # lost and a filled field would look like inert text.
                rect = (icon_el.x, icon_el.y,
                        icon_el.x + icon_el.width, icon_el.y + icon_el.height)
                for el, cov in zip(ocr_elements, coverages):
                    if cov >= _YOLO_TEXT_FUSE_THRESH:
                        el.interactive = True
                        known = el.control_rect   # of several boxes, the tightest
                        if known is None or ((rect[2] - rect[0]) * (rect[3] - rect[1])
                                             < (known[2] - known[0]) * (known[3] - known[1])):
                            el.control_rect = rect
                continue
            merged.append(icon_el)
        return merged

    # Control detection started ahead of need: (frame, thread, result box).
    _ahead: Optional[Tuple[Any, threading.Thread, Dict[str, Any]]] = None

    def _detect_ahead(self) -> None:
        """Start detecting the controls of the current frame in the background.
        Called when a planner call goes out: the plan that comes back usually
        begins with a field, and the seconds the planner takes would otherwise
        be idle. _need_controls takes the result if the frame is still the
        current one."""
        shot = self._prev_screenshot
        if self._controls_ready or shot is None or (
                self._ahead is not None and self._ahead[0] is shot):
            return
        box: Dict[str, Any] = {}

        def detect() -> None:
            try:
                box["icons"] = extract_icon_elements(shot)
            except Exception as exc:   # the step that needs them detects again
                box["error"] = exc

        worker = threading.Thread(target=detect, daemon=True, name="agent-controls")
        worker.start()
        self._ahead = (shot, worker, box)

    def _need_controls(self) -> None:
        """Detect the controls without text (empty fields, icons) on the current
        frame, if that has not happened yet, and mark text that sits inside a
        control. Called by the steps that work on fields and before the planner
        gets the screenshot."""
        if self._controls_ready or self._prev_screenshot is None or self._last_rect is None:
            return
        t0 = time.monotonic()
        win_x, win_y = self._last_rect[0], self._last_rect[1]
        icon_elements = None
        ahead, self._ahead = self._ahead, None
        if ahead is not None and ahead[0] is self._prev_screenshot:
            ahead[1].join()
            icon_elements = ahead[2].get("icons")
        if icon_elements is None:
            icon_elements = extract_icon_elements(self._prev_screenshot)
        for el in icon_elements:
            el.x += win_x
            el.y += win_y
            el.center_x += win_x
            el.center_y += win_y
        texts = [el for el in (self._last_elements or []) if el.source != "yolo"]
        # The text elements are the registry's own objects and keep their ids;
        # the change-set of this reconcile (controls "appearing") means nothing.
        reconciled, _ = self.registry.reconcile(
            self._merge_text_and_icons(texts, _below_title_bar(icon_elements)),
            self.width, self.height)
        self._last_elements = self._elements = reconciled
        self._controls_ready = True
        self._parses["controls"] += 1
        annotated = annotate_screenshot(self._prev_screenshot, reconciled, win_x, win_y,
                                        click_marker=self.executor.last_click_point)
        if self.cfg.save_debug_screenshots:
            self._save_debug(annotated, self._prev_screenshot, self._last_rect)
        buf = BytesIO()
        annotated.save(buf, format="PNG")
        self._shot_b64 = base64.b64encode(buf.getvalue()).decode()
        self._timing["perception"] += time.monotonic() - t0
        log.info("controls: %.2fs \u2192 %d without text", time.monotonic() - t0,
                 sum(1 for el in reconciled if el.source == "yolo"))

    def _settle(self, expect_large: bool, quiet: int = 1) -> None:
        """Wait until the screen has reacted to a click and stopped changing.

        A click shows at once on the clicked control (highlight, pressed
        state), while the view it opens can take a second to load. Read too
        early, the screen looks as if the click had done next to nothing — and
        a second click then lands on whatever the new view puts in that place.
        So: return as soon as two frames in a row are the same AND a large part
        of the window differs from before the click; otherwise keep watching
        until click_settle_s has passed. With expect_large False (a list is
        expected to open), any change that has stopped is enough. *quiet* is
        the number of unchanged frame pairs in a row that count as stopped."""
        before = self._prev_screenshot
        deadline = time.monotonic() + self.cfg.click_settle_s
        prev, _ = capture_foreground()
        calm = 0
        while True:
            time.sleep(0.2)
            cur, _ = capture_foreground()
            still = _changed_boxes(prev, cur)[1] < 0.002     # a blinking caret is less
            calm = calm + 1 if still else 0
            changed = _changed_boxes(before, cur)[1] if before is not None else 1.0
            if calm >= quiet and (changed >= 0.2 or (not expect_large and changed > 0)):
                return
            if time.monotonic() >= deadline and (still or time.monotonic() >= deadline + 1.5):
                return
            prev = cur

    def _reread_changed(
        self, screenshot: Image.Image, win_x: int, win_y: int
    ) -> Optional[List[Element]]:
        """Read only the regions that differ from the previous frame and keep
        the cached elements everywhere else. Returns the element list
        (screen-absolute), or None when so much changed that a full parse is
        the better deal.

        Every changed box is grown until it fully contains each cached element
        it touches — a box cutting through a word would re-read half of it —
        so the cached elements that are kept are exactly those no box touches."""
        boxes, frac = _changed_boxes(self._prev_screenshot, screenshot)
        if not boxes or frac > _PARTIAL_MAX_FRAC:
            return None
        W, H = screenshot.size
        cached = [(el, el.x - win_x, el.y - win_y,
                   el.x - win_x + el.width, el.y - win_y + el.height)
                  for el in self._last_elements]

        def touches(r: List[int], x1: int, y1: int, x2: int, y2: int) -> bool:
            return x1 < r[2] and x2 > r[0] and y1 < r[3] and y2 > r[1]

        rects = [[x - _PARTIAL_PAD, y - _PARTIAL_PAD, x + w + _PARTIAL_PAD, y + h + _PARTIAL_PAD]
                 for x, y, w, h in boxes]
        for _ in range(4):
            grown = False
            for r in rects:
                for _el, x1, y1, x2, y2 in cached:
                    if touches(r, x1, y1, x2, y2) and (
                            x1 < r[0] or y1 < r[1] or x2 > r[2] or y2 > r[3]):
                        r[0], r[1] = min(r[0], x1 - 2), min(r[1], y1 - 2)
                        r[2], r[3] = max(r[2], x2 + 2), max(r[3], y2 + 2)
                        grown = True
            rects = _merge_rects(rects)
            if not grown:
                break
        rects = [[max(0, r[0]), max(0, r[1]), min(W, r[2]), min(H, r[3])] for r in rects]
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        if area > _PARTIAL_MAX_FRAC * W * H:
            return None

        # Kept as they are: the elements no re-read region touches, and those
        # inside a region whose own pixels did not change. Reading the latter
        # again gives the same text at best and a different one at worst
        # ('Last Name *' came back as 'Last Name' and passed for a change).
        changed = [[x, y, x + w, y + h] for x, y, w, h in boxes]
        kept = [el for el, x1, y1, x2, y2 in cached
                if not any(touches(r, x1, y1, x2, y2) for r in rects)
                or not any(touches(c, x1, y1, x2, y2) for c in changed)]
        kept_boxes = [(el.x, el.y, el.x + el.width, el.y + el.height) for el in kept]
        scale = _YOLO_IMGSZ / max(W, H)   # the pixel scale a full parse runs the detector at
        fresh: List[Element] = []
        for x1, y1, x2, y2 in rects:
            crop = screenshot.crop((x1, y1, x2, y2))
            ocr_elements = extract_ocr_elements(crop, crop=True)
            # Controls only where the rest of the frame has them too.
            icon_elements = (extract_icon_elements(crop, scale=scale)
                             if self._controls_ready else [])
            for el in ocr_elements + icon_elements:
                el.x += x1 + win_x
                el.y += y1 + win_y
                el.center_x += x1 + win_x
                el.center_y += y1 + win_y
            # A re-read of something that was kept is dropped: its centre lies
            # in a kept element's box.
            fresh.extend(
                el for el in self._merge_text_and_icons(ocr_elements, icon_elements)
                if not any(bx1 <= el.center_x <= bx2 and by1 <= el.center_y <= by2
                           and (el.source == "yolo") == (k.source == "yolo")
                           for k, (bx1, by1, bx2, by2) in zip(kept, kept_boxes)))
        log.info("changed: %d region(s), %.0f%% of the window \u2192 %d elements re-read, %d kept",
                 len(rects), 100.0 * area / (W * H), len(fresh), len(kept))
        return kept + fresh

    def _carry_controls(self, screenshot: Image.Image, win_x: int, win_y: int
                        ) -> Optional[List[Element]]:
        """The controls of the previous frame, for this one: those whose own
        pixels did not change, as they were, and what the detector finds now
        in the regions that did change. Window-relative, like a fresh
        detection. None if so much changed that the detector is better run on
        the whole window (a scroll, another view): nothing is carried then.

        Known controls are the boxes without text and the boxes of controls
        whose text stands for them. Every changed region is grown until it
        fully contains each known control it touches, so that the detector
        never sees half a control."""
        t0 = time.monotonic()
        boxes, _frac = _changed_boxes(self._prev_screenshot, screenshot)
        if not boxes:
            return None
        W, H = screenshot.size
        known: List[Tuple[int, int, int, int]] = []
        for el in self._last_elements:
            if el.source == "yolo":
                box = (el.x, el.y, el.x + el.width, el.y + el.height)
            elif el.control_rect:
                box = tuple(el.control_rect)
            else:
                continue
            box = (box[0] - win_x, box[1] - win_y, box[2] - win_x, box[3] - win_y)
            if box not in known:
                known.append(box)

        def touches(r, k) -> bool:
            return k[0] < r[2] and k[2] > r[0] and k[1] < r[3] and k[3] > r[1]

        changed = [(x, y, x + w, y + h) for x, y, w, h in boxes]
        kept = [k for k in known if not any(touches(c, k) for c in changed)]
        rects = [[x1 - _PARTIAL_PAD, y1 - _PARTIAL_PAD, x2 + _PARTIAL_PAD, y2 + _PARTIAL_PAD]
                 for x1, y1, x2, y2 in changed]
        for _ in range(4):
            grown = False
            for r in rects:
                for k in known:
                    if k not in kept and touches(r, k) and (
                            k[0] < r[0] or k[1] < r[1] or k[2] > r[2] or k[3] > r[3]):
                        r[0], r[1] = min(r[0], k[0] - 2), min(r[1], k[1] - 2)
                        r[2], r[3] = max(r[2], k[2] + 2), max(r[3], k[3] + 2)
                        grown = True
            rects = _merge_rects(rects)
            if not grown:
                break
        rects = [[max(0, r[0]), max(0, r[1]), min(W, r[2]), min(H, r[3])] for r in rects]
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        if area > _PARTIAL_MAX_FRAC * W * H:
            log.info("controls not carried over: %.0f%% of the window would have to be looked "
                     "at again", 100.0 * area / (W * H))
            return None
        scale = _YOLO_IMGSZ / max(W, H)   # the pixel scale a full detection runs at
        icons = [Element(stable_id="", text="", control_type="Icon", source="yolo",
                         x=x1, y=y1, width=x2 - x1, height=y2 - y1,
                         center_x=(x1 + x2) // 2, center_y=(y1 + y2) // 2)
                 for x1, y1, x2, y2 in kept]
        found = 0
        for x1, y1, x2, y2 in rects:
            for el in extract_icon_elements(screenshot.crop((x1, y1, x2, y2)), scale=scale):
                el.x += x1
                el.y += y1
                el.center_x += x1
                el.center_y += y1
                # A control that was kept is not taken a second time.
                if not any(k[0] <= el.center_x <= k[2] and k[1] <= el.center_y <= k[3]
                           for k in kept):
                    icons.append(el)
                    found += 1
        log.info("controls carried over: %d as they were, %d found again in %d region(s), "
                 "%.0f%% of the window (%.2fs)", len(kept), found, len(rects),
                 100.0 * area / (W * H), time.monotonic() - t0)
        return icons

    def _save_debug(self, annotated: Image.Image, raw: Image.Image,
                    rect: Tuple[int, int, int, int]) -> None:
        """Write the annotated image to the shared debug path and the frame as
        captured next to it (the UI draws its own marks on that one). *rect* is
        the window's place on screen: click coordinates are screen-absolute, a
        point in either image is (x - rect.x, y - rect.y)."""
        try:
            annotated.save(_debug_screenshot_path(), format="PNG")
            raw.save(_raw_screenshot_path(), format="PNG", compress_level=1)
            log.info("[SCREENSHOT_READY] %s", json.dumps(
                dict(zip(("x", "y", "w", "h"), rect)), separators=(",", ":")))
        except Exception as exc:
            log.debug("debug screenshot save failed: %s", exc)

    @staticmethod
    def _format_elements(elements: List[Element], limit: int = 200,
                         include_yolo: bool = True) -> str:
        """Format elements into two labeled sections: OCR text and YOLO icons.

        include_yolo=False omits the INTERACTIVE REGIONS section — YOLO lines
        carry no text, so they are pure prefill cost for roles that never click
        (the supervisor plans from labels/values, not from anonymous boxes)."""
        ocr_els = sorted(
            [el for el in elements if el.source != "yolo"],
            key=lambda el: (el.center_y // 16, el.center_x),
        )
        yolo_els = sorted(
            [el for el in elements if el.source == "yolo"],
            key=lambda el: (el.center_y // 16, el.center_x),
        )
        parts: List[str] = []

        ocr_lines = [el.as_prompt_line() for el in ocr_els[:limit]]
        if len(ocr_els) > limit:
            ocr_lines.append(f"... (+{len(ocr_els) - limit} more, not shown)")
        legend = (" (a text marked <interactive, in a control WxHpx> is what that control "
                  "shows: a button's caption, or the content of a field that is already "
                  "filled; text and control are one thing)"
                  if any(el.interactive for el in ocr_els[:limit]) else "")
        parts.append(
            f"OCR TEXT ELEMENTS{legend}:\n" + ("\n".join(ocr_lines) if ocr_lines else "(none)")
        )

        if yolo_els and include_yolo:
            yolo_limit = 100
            yolo_lines = [el.as_prompt_line() for el in yolo_els[:yolo_limit]]
            if len(yolo_els) > yolo_limit:
                yolo_lines.append(f"... (+{len(yolo_els) - yolo_limit} more, not shown)")
            parts.append(
                "INTERACTIVE REGIONS WITHOUT TEXT (empty fields, icons):\n"
                + "\n".join(yolo_lines)
            )

        return "\n\n".join(parts) if parts else "(no elements detected)"

    _CONTEXT_DUMP_PATH = Path(tempfile.gettempdir()) / "agent_context_dump.txt"
