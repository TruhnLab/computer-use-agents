"""The window in front: finding it, taking its picture, removing the blinking text
cursor, comparing two pictures, and drawing the annotated screenshot."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple

import numpy as np
import pyautogui
from PIL import Image, ImageDraw, ImageFont

from .elements import Element

log = logging.getLogger("agent")


# --- Screen capture (foreground window only) ------------------------------ #

def _true_window_rect_windows(hwnd) -> Optional[Tuple[int, int, int, int]]:
    """Return (x, y, w, h) using DWM extended frame bounds (excludes drop shadow)."""
    try:
        import ctypes
        import ctypes.wintypes
        dwmapi = ctypes.windll.dwmapi
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        rect = ctypes.wintypes.RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr != 0:
            return None
        x, y = rect.left, rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 0 and h > 0:
            return (x, y, w, h)
    except Exception as exc:
        log.debug("DwmGetWindowAttribute failed: %s", exc)
    return None


def _foreground_rect_win() -> Optional[Tuple[int, int, int, int]]:
    """Screen-absolute (x, y, w, h) of the foreground window on Windows, or None."""
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    # Prefer DWM extended frame bounds (excludes drop shadow).
    rect = _true_window_rect_windows(hwnd)
    if rect is None:
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
    x, y, w, h = rect
    return (x, y, w, h) if (w > 0 and h > 0) else None


def _title_bar_bottom() -> Optional[int]:
    """Screen y where the title bar of the foreground window ends, or None if
    it has none of its own (not Windows, a window that draws its own frame).

    The strip above it holds the window's caption buttons - minimise,
    maximise, close - and OCR reads them like any other text ('×', '_').
    Only the caption is cut off, not the whole non-client area: a classic
    menu bar belongs to that area too, and has to stay clickable."""
    if platform.system() != "Windows":
        return None
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = _true_window_rect_windows(hwnd)
    origin = ctypes.wintypes.POINT(0, 0)
    if rect is None or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    non_client = origin.y - rect[1]
    if non_client <= 0:
        return None
    # caption + sizing frame + padded border (SM_CYCAPTION, SM_CYSIZEFRAME, SM_CXPADDEDBORDER)
    caption = (user32.GetSystemMetrics(4) + user32.GetSystemMetrics(33)
               + user32.GetSystemMetrics(92))
    return rect[1] + min(non_client, caption)


def _below_title_bar(elements: List[Element]) -> List[Element]:
    """The elements without those in the window's title bar."""
    bottom = _title_bar_bottom()
    if bottom is None:
        return elements
    return [el for el in elements if el.center_y >= bottom]


def _foreground_window() -> Optional[Tuple[int, str]]:
    """(process id, title) of the window now in the foreground, or None where
    that cannot be told (not Windows, no foreground window)."""
    if platform.system() != "Windows":
        return None
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return pid.value, buf.value


def capture_foreground() -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """
    Screenshot only the foreground window. Returns (image, (x, y, w, h)) where
    x, y are screen-absolute. OCR coords from the image must be offset by
    (x, y) to become screen-absolute before clicking.
    """
    if platform.system() == "Windows":
        try:
            rect = _foreground_rect_win()
            if rect is not None:
                x, y, w, h = rect
                img = pyautogui.screenshot(region=(x, y, w, h))
                return img, (x, y, w, h)
        except Exception as exc:
            log.warning("Foreground window capture failed (%s); falling back to full screen.", exc)
        import ctypes
        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        try:
            return pyautogui.screenshot(region=(0, 0, sw, sh)), (0, 0, sw, sh)
        except OSError as exc:
            # Windows hands out no picture of a desktop nobody can see.
            raise RuntimeError(
                "The screen cannot be captured: the workstation is locked, or its display "
                f"session is disconnected ({exc}). Unlock it and start the request again."
            ) from exc

    if platform.system() == "Darwin":
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        try:
            subprocess.run(["screencapture", "-x", path], check=True)
            img = Image.open(path).copy()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        w, h = img.size
        return img, (0, 0, w, h)

    img = pyautogui.screenshot()
    w, h = img.size
    return img, (0, 0, w, h)


# --- Caret removal (temporal de-flicker before OCR) ------------------------ #
#
# A focused edit field draws a blinking text caret; RapidOCR reads its thin
# vertical stroke as 'l'/'I'/'|'/'1', and when it abuts typed text it merges
# into the word ("Doe" -> "Doel"). Post-OCR filtering cannot undo the merge, so
# we remove the caret *before* OCR: capture a few frames across the blink cycle,
# find pixels that change between them (the caret, plus any incidental
# animation), and inpaint those regions from their surroundings. Polarity-
# agnostic (fills from neighbouring background), so it works for a dark caret on
# a light field or vice versa. Requires the caret to blink (>=1 'off' frame).

# 4 frames x 0.18s spans 0.54s — just over the ~0.53s ON phase of the standard
# Windows caret blink, so at least one caret-OFF frame is guaranteed. (3 frames
# spanned only 0.36s and could land entirely inside the ON phase, letting the
# caret survive into OCR as a phantom 'l'/'I'.)
_DECARET_FRAMES = int(os.getenv("DECARET_FRAMES", "4"))


_DECARET_INTERVAL_S = float(os.getenv("DECARET_INTERVAL_S", "0.18"))


_DECARET_DIFF_THRESH = int(os.getenv("DECARET_DIFF_THRESH", "26"))  # 0-255 grayscale delta


_DECARET_RING_PX = int(os.getenv("DECARET_RING_PX", "3"))  # width of the surrounding ring sampled per region


def capture_foreground_burst(
    n: int = _DECARET_FRAMES, interval_s: float = _DECARET_INTERVAL_S
) -> Tuple[List[Image.Image], Tuple[int, int, int, int]]:
    """Capture *n* screenshots of the foreground window *interval_s* apart, all
    aligned to the same rect, so a blinking caret is caught in multiple phases.
    Returns (frames, (x, y, w, h)). Falls back to a single frame on any error."""
    if platform.system() == "Windows":
        try:
            rect = _foreground_rect_win()
            if rect is not None:
                x, y, w, h = rect
                frames: List[Image.Image] = []
                for i in range(max(1, n)):
                    if i:
                        time.sleep(interval_s)
                    frames.append(pyautogui.screenshot(region=(x, y, w, h)))
                return frames, rect
        except Exception as exc:
            log.warning("Foreground burst capture failed (%s); single frame.", exc)
    img, rect = capture_foreground()
    return [img], rect


def _decaret(frames: List[Image.Image]) -> Tuple[Image.Image, List[Tuple[int, int]]]:
    """Return (healed_image, caret_centers): the first frame with the blinking
    caret (and any other pixels that changed between frames) replaced by the
    REAL pixels from the frame where the caret is off — never a synthesised/
    inpainted colour — plus the centers (image-relative x, y) of healed regions
    whose shape looks like a text caret (thin vertical bar). The caret is the
    ONLY visible evidence that a click focused an edit field, and healing it
    erases that evidence from the screenshot — so it is reported instead.

    Method: diff the frames to a change mask, group it into regions (each a
    blinking element), and for each region sample the field colour in a thin ring
    that FOLLOWS THE REGION'S SHAPE (dilate(region) - region), excluding every
    other changed pixel so a neighbouring blinker cannot pollute it. Then pick the
    frame whose region best matches that surrounding colour (the caret-off frame)
    and copy its real pixels. This stays correct when the caret abuts text
    ('Doe|' -> 'Doe') and when several elements blink independently. Needs the
    caret off in >=1 frame; with <2 frames or on any error, returns frame 0."""
    if not frames:
        raise ValueError("_decaret: no frames")
    ref = frames[0]
    if len(frames) < 2:
        return ref, []
    try:
        import cv2
        arrs = [np.asarray(f.convert("RGB")).astype(np.uint8) for f in frames]
        h = min(a.shape[0] for a in arrs)
        w = min(a.shape[1] for a in arrs)
        arrs = [a[:h, :w] for a in arrs]                 # guard off-by-one size drift
        stack = np.stack(arrs, axis=0).astype(np.int16)  # (N, h, w, 3)
        lum = stack.mean(axis=3)                          # (N, h, w)
        spread = lum.max(axis=0) - lum.min(axis=0)        # per-pixel temporal range
        mask = (spread > _DECARET_DIFF_THRESH).astype(np.uint8)
        changed = int(mask.sum())
        if changed == 0:
            return ref, []
        ring_k = np.ones((2 * _DECARET_RING_PX + 1, 2 * _DECARET_RING_PX + 1), np.uint8)
        n_labels, labels = cv2.connectedComponents(mask, connectivity=8)
        out = arrs[0].copy()
        carets: List[Tuple[int, int]] = []
        for lbl in range(1, n_labels):
            region = labels == lbl
            # Caret-shaped blinker? Thin vertical bar: much taller than wide,
            # text-line sized. Recorded BEFORE healing erases the evidence.
            ys, xs = np.nonzero(region)
            rw = int(xs.max() - xs.min() + 1)
            rh = int(ys.max() - ys.min() + 1)
            if rw <= 8 and 8 <= rh <= 64 and rh >= 2 * rw:
                carets.append((int(xs.mean()), int(ys.mean())))
            # Shape-following ring just outside the region; never sample another
            # changed pixel (mask == 0), so a nearby blinker can't bias the colour.
            ring = (cv2.dilate(region.astype(np.uint8), ring_k) > 0) & (mask == 0)
            if not ring.any():
                continue
            ref_color = np.median(stack[0][ring], axis=0)   # surrounding field colour
            best = min(
                range(len(arrs)),
                key=lambda f, _r=region, _c=ref_color: float(
                    np.abs(np.median(stack[f][_r], axis=0) - _c).sum()
                ),
            )
            out[region] = arrs[best][region]                # copy the caret-off frame's real pixels
        log.info("decaret: healed %d changed px in %d region(s) across %d frames%s",
                 changed, n_labels - 1, len(frames),
                 f"; caret detected at {carets[0]}" if carets else "")
        return Image.fromarray(out), carets
    except Exception as exc:
        log.warning("_decaret failed (%s: %s) — using raw frame.", type(exc).__name__, exc)
        return ref, []


# --- Image annotation ------------------------------------------------------ #

def _get_font(size: int = 11) -> Optional[ImageFont.ImageFont]:
    """Small truetype font if available, else default bitmap font."""
    candidates = [
        "arial.ttf", "Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _diff_regions(
    prev: "Image.Image",
    cur: "Image.Image",
    cell: int = 24,
    pixel_thresh: int = 28,
    min_cell_frac: float = 0.06,
    max_regions: int = 12,
) -> List[Tuple[int, int, int, int]]:
    """Coarse pixel-diff between two same-size frames → changed-region rectangles.

    Grayscale abs-diff is thresholded, pooled into a cell grid, and adjacent
    changed cells are merged (4-neighbour connected components). Returns up to
    `max_regions` (x, y, w, h) boxes in image coords, largest first. Full-frame
    regions (>60% area — i.e. the whole screen changed) are dropped; the caller
    already skips this entirely on scroll/replaced transitions.
    """
    if prev.size != cur.size:
        return []
    a = np.asarray(prev.convert("L"), dtype=np.int16)
    b = np.asarray(cur.convert("L"), dtype=np.int16)
    diff = np.abs(a - b) > pixel_thresh          # bool HxW
    if not diff.any():
        return []
    H, W = diff.shape
    gh, gw = (H + cell - 1) // cell, (W + cell - 1) // cell
    padded = np.zeros((gh * cell, gw * cell), dtype=np.int32)
    padded[:H, :W] = diff
    counts = padded.reshape(gh, cell, gw, cell).sum(axis=(1, 3))
    grid = counts >= int(cell * cell * min_cell_frac)

    visited = np.zeros_like(grid)
    regions: List[Tuple[int, int, int, int, int]] = []
    for cy in range(gh):
        for cx in range(gw):
            if not grid[cy, cx] or visited[cy, cx]:
                continue
            stack = [(cy, cx)]
            visited[cy, cx] = True
            minx = maxx = cx
            miny = maxy = cy
            while stack:
                yy, xx = stack.pop()
                minx, maxx = min(minx, xx), max(maxx, xx)
                miny, maxy = min(miny, yy), max(maxy, yy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = yy + dy, xx + dx
                    if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            rx, ry = minx * cell, miny * cell
            rw = min((maxx + 1) * cell, W) - rx
            rh = min((maxy + 1) * cell, H) - ry
            area = rw * rh
            if area > 0.6 * W * H:      # whole-screen change — not a useful highlight
                continue
            regions.append((rx, ry, rw, rh, area))
    regions.sort(key=lambda r: -r[4])
    return [(x, y, w, h) for (x, y, w, h, _) in regions[:max_regions]]


# Share of the window that may have changed for a partial parse; above it the
# full parse is the better deal (and a view change must be read as a whole).
_PARTIAL_MAX_FRAC = float(os.getenv("PARTIAL_PARSE_MAX_FRAC", "0.35"))


_PARTIAL_PAD = 16   # px read around every changed box


def _changed_boxes(
    prev: "Image.Image", cur: "Image.Image", cell: int = 16
) -> Tuple[List[Tuple[int, int, int, int]], float]:
    """Exact pixel diff between two frames → (boxes, changed_fraction).

    Unlike _diff_regions (a tolerant highlight for the annotated screenshot),
    this misses nothing: a cell counts as changed when a single pixel differs
    in any channel, because the boxes decide which parts of the screen are read
    again, and a change left unread would leave a stale element behind. Changed
    cells are grouped into 8-connected components and returned as (x, y, w, h)
    in image coords; changed_fraction is the share of cells that changed.
    Frames of different size count as fully changed."""
    a = np.asarray(prev.convert("RGB"), dtype=np.uint8)
    b = np.asarray(cur.convert("RGB"), dtype=np.uint8)
    if a.shape != b.shape:
        return [], 1.0
    diff = (a != b).any(axis=2)
    if not diff.any():
        return [], 0.0
    import cv2
    H, W = diff.shape
    gh, gw = (H + cell - 1) // cell, (W + cell - 1) // cell
    padded = np.zeros((gh * cell, gw * cell), dtype=bool)
    padded[:H, :W] = diff
    grid = padded.reshape(gh, cell, gw, cell).any(axis=(1, 3))
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        grid.astype(np.uint8), connectivity=8)
    boxes: List[Tuple[int, int, int, int]] = []
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT]) * cell
        y = int(stats[i, cv2.CC_STAT_TOP]) * cell
        boxes.append((x, y,
                      min(int(stats[i, cv2.CC_STAT_WIDTH]) * cell, W - x),
                      min(int(stats[i, cv2.CC_STAT_HEIGHT]) * cell, H - y)))
    return boxes, float(grid.mean())


def _merge_rects(rects: List[List[int]]) -> List[List[int]]:
    """Union overlapping [x1, y1, x2, y2] rectangles until none overlap."""
    rects = [list(r) for r in rects]
    merged = True
    while merged:
        merged = False
        out: List[List[int]] = []
        for r in rects:
            for o in out:
                if r[0] < o[2] and r[2] > o[0] and r[1] < o[3] and r[3] > o[1]:
                    o[0], o[1] = min(o[0], r[0]), min(o[1], r[1])
                    o[2], o[3] = max(o[2], r[2]), max(o[3], r[3])
                    merged = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def annotate_screenshot(
    screenshot: Image.Image,
    elements: List[Element],
    win_x: int,
    win_y: int,
    click_marker: Optional[Tuple[int, int]] = None,
    max_label_elements: int = 120,
    highlight_regions: Optional[List[Tuple[int, int, int, int]]] = None,
) -> Image.Image:
    """
    Draw OCR bounding boxes + stable IDs onto the screenshot, and optionally
    a crosshair at click_marker (screen-absolute coords).

    highlight_regions: image-relative (x, y, w, h) rectangles marking pixels that
    changed since the previous frame (from the pixel-diff). Drawn UNDER the
    element boxes as a translucent magenta wash so the model's eye is pulled to
    "what just changed" — catching visual-state changes OCR/YOLO cannot see
    (a button greying out, a spinner, a red validation border).

    Returns a new RGB image. Input is not modified.
    """
    out = screenshot.convert("RGB").copy()
    draw = ImageDraw.Draw(out, "RGBA")
    font = _get_font(11)

    # Change-highlight wash first, so element boxes/labels stay legible on top.
    for (rx, ry, rw, rh) in (highlight_regions or []):
        draw.rectangle([rx, ry, rx + rw, ry + rh], fill=(255, 0, 200, 40),
                       outline=(255, 0, 200, 230), width=2)
        if font is not None:
            draw.text((rx + 2, max(0, ry - 12)), "Δ changed", fill=(255, 120, 220), font=font)

    # Sort elements by confidence; label only the top N to avoid visual clutter
    # on very dense screens. Low-confidence elements still get a faint box.
    elems_sorted = sorted(elements, key=lambda el: -el.confidence)

    for i, el in enumerate(elems_sorted):
        ix = el.x - win_x
        iy = el.y - win_y
        iw = el.width
        ih = el.height

        # Faint box for every element; brighter for high-confidence / labeled ones.
        # OCR elements: orange. YOLO icon elements — and OCR text fused with a
        # YOLO control (text inside a clickable field/button): blue.
        is_yolo = el.source == "yolo"
        is_clickable = is_yolo or el.interactive
        color_full = (50, 150, 255, 220) if is_clickable else (255, 140, 0, 220)
        color_faint = (50, 150, 255, 90) if is_clickable else (255, 140, 0, 90)
        label_color = (130, 210, 255) if is_clickable else (255, 200, 80)
        # Always label YOLO elements (they're few and high-value); OCR elements
        # need confidence >= 40 to earn a label (avoids cluttering low-conf noise).
        show_label = i < max_label_elements and (is_yolo or el.confidence >= 40)
        if show_label:
            draw.rectangle([ix, iy, ix + iw, iy + ih], outline=color_full, width=1)
            # ID label above the box (or below if near top edge).
            label = el.stable_id
            label_y = iy - 12 if iy >= 14 else iy + ih + 1
            # Text with a thin dark backdrop for legibility on any background.
            if font is not None:
                try:
                    tw = draw.textlength(label, font=font)
                except Exception:
                    tw = 8 * len(label)
                th = 11
                draw.rectangle(
                    [ix, label_y, ix + tw + 3, label_y + th + 2],
                    fill=(0, 0, 0, 180),
                )
                draw.text((ix + 2, label_y), label, fill=label_color, font=font)
            else:
                draw.text((ix + 2, max(0, label_y)), label, fill=label_color)
        else:
            draw.rectangle([ix, iy, ix + iw, iy + ih], outline=color_faint, width=1)

    # Crosshair at last-click location.
    if click_marker is not None:
        cx, cy = click_marker[0] - win_x, click_marker[1] - win_y
        r = 10
        # Cyan circle + crosshair, with dark halo for contrast.
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 0, 0, 220), width=3)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 255, 255, 255), width=2)
        draw.line([cx - r - 4, cy, cx + r + 4, cy], fill=(0, 255, 255, 255), width=2)
        draw.line([cx, cy - r - 4, cx, cy + r + 4], fill=(0, 255, 255, 255), width=2)
        if font is not None:
            draw.text((cx + r + 6, cy - 6), "last click", fill=(0, 255, 255), font=font)

    return out


def _changed_cells(prev: "Image.Image", cur: "Image.Image", cell: int = 16,
                   tolerance: int = 0, drop_lone: bool = False
                   ) -> Optional[List[Tuple[int, int, int, int]]]:
    """The cells of a 16 px grid in which two frames differ, as (x1, y1, x2,
    y2) in image coordinates. None if the frames differ in size.

    As called without more, any pixel that differs in any channel counts.
    *tolerance*: a pixel counts only if one of its channels differs by MORE
    than this many steps of 255 - per channel, not by brightness, so that a
    colour turning into another of equal brightness is still a change.
    *drop_lone*: a changed pixel none of whose eight neighbours changed is
    left out; two changed pixels side by side count."""
    a = np.asarray(prev.convert("RGB"), dtype=np.uint8)
    b = np.asarray(cur.convert("RGB"), dtype=np.uint8)
    if a.shape != b.shape:
        return None
    if tolerance > 0:
        diff = (np.abs(a.astype(np.int16) - b.astype(np.int16)) > tolerance).any(axis=2)
    else:
        diff = (a != b).any(axis=2)
    if drop_lone and diff.any():
        p = np.pad(diff, 1)
        diff = diff & (p[:-2, :-2] | p[:-2, 1:-1] | p[:-2, 2:] | p[1:-1, :-2]
                       | p[1:-1, 2:] | p[2:, :-2] | p[2:, 1:-1] | p[2:, 2:])
    H, W = diff.shape
    gh, gw = (H + cell - 1) // cell, (W + cell - 1) // cell
    padded = np.zeros((gh * cell, gw * cell), dtype=bool)
    padded[:H, :W] = diff
    grid = padded.reshape(gh, cell, gw, cell).any(axis=(1, 3))
    return [(int(cx) * cell, int(cy) * cell, (int(cx) + 1) * cell, (int(cy) + 1) * cell)
            for cy, cx in np.argwhere(grid)]


def typing_only_changed(
    changed: List[Tuple[int, int, int, int]], field: Tuple[int, int, int, int],
    allowed: List[Tuple[int, int, int, int]], pad: int = 8,
) -> Tuple[bool, str]:
    """Did typing into a field change the screen the way typing does?

    *changed* are the cells in which pixels differ between the frame before
    the click and the frame after the typing, *field* is the control typed
    into, *allowed* the boxes typing may touch: that control, its label, and
    control and label of the field that had the focus before. All boxes are
    (x1, y1, x2, y2) on screen. Expected is: every changed cell touches an
    allowed box (grown by *pad* px for a focus glow and an inexact box), and
    at least one touches the field itself. Anything else - a list or a
    calendar that opened, a dialog, a layout that moved, text in another
    place, or no change at all - is not, and the caller reads the screen.
    Returns (expected, what was different)."""
    def touches(c: Tuple[int, int, int, int], box: Tuple[int, int, int, int], grow: int) -> bool:
        return (c[0] < box[2] + grow and box[0] - grow < c[2]
                and c[1] < box[3] + grow and box[1] - grow < c[3])
    outside = [c for c in changed if not any(touches(c, box, pad) for box in allowed)]
    if outside:
        x1, y1 = min(c[0] for c in outside), min(c[1] for c in outside)
        x2, y2 = max(c[2] for c in outside), max(c[3] for c in outside)
        return False, (f"the screen changed outside the field, within ({x1},{y1})-({x2},{y2}), "
                       f"{len(outside)} cells")
    if not any(touches(c, field, 0) for c in changed):
        return False, "nothing changed in the field"
    return True, ""
