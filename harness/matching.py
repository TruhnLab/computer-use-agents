"""Checks on the text of a screen that need no model: labels, values next to their
labels, typed values, and the evidence for a completion."""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .elements import ChangeSet, Element
from .plan import Step

log = logging.getLogger("agent")


# --- Evidence check (generic task completion verification) --------------- #

def _extract_meaningful_words(text: str) -> List[str]:
    """Extract lowercased words of length >=4 OR any token containing digits.
    These are the tokens we require to be findable in OCR."""
    tokens = re.findall(r"[A-Za-zÄÖÜäöüß0-9]+", text)
    out = []
    for t in tokens:
        if any(ch.isdigit() for ch in t) or len(t) >= 4:
            out.append(t.lower())
    return out


def _best_fuzzy_match(word: str, ocr_blob: str) -> Tuple[float, str]:
    """Find the best fuzzy match for `word` in the OCR blob. Returns (ratio, matched_token)."""
    best_ratio = 0.0
    best_tok = ""
    # Scan words of similar length (±2) for efficiency.
    for tok in re.findall(r"[A-Za-zÄÖÜäöüß0-9]+", ocr_blob):
        if abs(len(tok) - len(word)) > 2:
            continue
        r = difflib.SequenceMatcher(None, word, tok.lower()).ratio()
        if r > best_ratio:
            best_ratio = r
            best_tok = tok
    return best_ratio, best_tok


def check_evidence_in_ocr(
    evidence: List[str],
    elements: List[Element],
    min_hit_ratio: float = 0.6,
    fuzzy_threshold: float = 0.82,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    For each evidence string, check whether a sufficient fraction of its
    meaningful words are present in the current OCR (exact or fuzzy match).

    Returns (all_passed, details) where `details` is a list of per-evidence
    dicts with keys 'evidence', 'passed', 'missing', 'near_misses'.
    Near-misses are surfaced so the model can see "you said 'Mustermann' but
    the screen reads 'Mustermnn' — probably a typing or OCR issue".
    """
    ocr_blob = " ".join(el.text for el in elements)
    ocr_blob_lower = ocr_blob.lower()

    details: List[Dict[str, Any]] = []
    all_passed = True

    for claim in evidence:
        claim = (claim or "").strip()
        if not claim:
            details.append({"evidence": claim, "passed": False,
                            "missing": ["(empty evidence string)"], "near_misses": []})
            all_passed = False
            continue

        words = _extract_meaningful_words(claim)
        if not words:
            # No substantive words to check — be lenient and accept, but note it.
            details.append({"evidence": claim, "passed": True,
                            "missing": [], "near_misses": [],
                            "note": "no checkable tokens"})
            continue

        missing: List[str] = []
        near_misses: List[Tuple[str, str, float]] = []  # (word, matched, ratio)
        for w in words:
            if w in ocr_blob_lower:
                continue
            ratio, tok = _best_fuzzy_match(w, ocr_blob)
            if ratio >= fuzzy_threshold:
                near_misses.append((w, tok, ratio))
                # Near-miss counts as a hit for the ratio, but we record it.
                continue
            missing.append(w)

        hit = len(words) - len(missing)
        passed = (hit / len(words)) >= min_hit_ratio
        if not passed:
            all_passed = False

        details.append({
            "evidence": claim,
            "passed": passed,
            "missing": missing,
            "near_misses": [f"'{w}' ≈ '{t}'" for w, t, _ in near_misses],
        })

    return all_passed, details


def log_evidence_summary(evidence: List[str], details: List[Dict[str, Any]]) -> None:
    """Short INFO log describing the evidence check outcome."""
    passed = sum(1 for d in details if d["passed"])
    log.info("evidence check: %d/%d passed (%d items total)",
             passed, len(details), len(evidence))
    for d in details:
        mark = "✓" if d["passed"] else "✗"
        extra = ""
        if d.get("missing"):
            extra = f"  missing: {', '.join(d['missing'])}"
        if d.get("near_misses"):
            extra += f"  near: {'; '.join(d['near_misses'])}"
        log.info("  %s %r%s", mark, d["evidence"], extra)


def format_evidence_rejection(details: List[Dict[str, Any]]) -> str:
    """Build a message telling the model exactly which evidence items failed."""
    lines = ["Evidence check FAILED — your cited evidence was not findable in OCR:"]
    for d in details:
        if d["passed"]:
            continue
        lines.append(f"  • '{d['evidence']}'")
        if d["missing"]:
            lines.append(f"      missing words: {', '.join(d['missing'])}")
        if d["near_misses"]:
            lines.append(f"      near-misses (typo / OCR garble?): {'; '.join(d['near_misses'])}")
    lines.append(
        "Look carefully at the current screen. Either (a) the action did not "
        "produce what you claimed — keep working — or (b) OCR garbled the text, "
        "in which case re-cite evidence using what OCR actually shows."
    )
    return "\n".join(lines)


# --- Spatial evidence (label-anchored checks + wrong-field guard) ---------- #
#
# Presence-only evidence has a hole: a value typed into the WRONG field still
# passes, because the text is on screen — just in the wrong place. These helpers
# anchor a value to the field label it must sit next to, using pure geometry on
# the OCR elements (no LLM cost):
#   * check_value_near_label — used to tick a todo item and for final evidence.
#   * guard_wrong_field      — runs after every action on the deterministic
#     change-set; fires an alarm the moment a value lands next to a label other
#     than the expected one (the #1 form-filling mistake).

def _fuzzy_text_match(needle: str, hay: str, thresh: float = 0.8) -> bool:
    """True if `needle` matches `hay` (containment or whole-string fuzzy)."""
    n = (needle or "").strip().lower()
    h = (hay or "").strip().lower()
    if not n or not h:
        return False
    if n in h or h in n:
        return True
    return difflib.SequenceMatcher(None, n, h).ratio() >= thresh


def _value_in_text(value: str, text: str) -> bool:
    """Does the element `text` show `value`?

    Yes if the text is the value (spacing, case and OCR look-alikes aside), or
    contains it as whole tokens in a row ('John' in 'John Doe', a date inside
    a sentence). Not if its characters merely occur somewhere: '1-0-0' is not
    in '11/16/2026', 'Male' is not in 'Female'. A value OCR has garbled beyond
    its look-alikes is not found here; the caller then looks at what changed
    next to the label and reports the difference."""
    v = "".join((value or "").split()).casefold()
    t = "".join((text or "").split()).casefold()
    if not v or not t:
        return False
    if v == t or typed_matches(value, text)[0]:
        return True
    vt = re.findall(r"[^\W_]+", (value or "").casefold())
    tt = re.findall(r"[^\W_]+", (text or "").casefold())
    return bool(vt) and any(tt[i:i + len(vt)] == vt for i in range(len(tt) - len(vt) + 1))


def _label_in_text(label: str, text: str) -> bool:
    """Does `text` begin with (or contain as words) the field label?

    Catches the very common OCR merge of label and value into ONE element
    ('Id: P00004' → 'IdP00004', 'Nachname: Doe' → 'Nachname Doe'), where
    box-to-box adjacency can never match."""
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[:*]", " ", (s or "").lower()).split())
    ln, tn = norm(label), norm(text)
    if not ln or not tn:
        return False
    if f" {ln} " in f" {tn} ":
        return True
    # Read together without a space: the label, then a digit or a new
    # capitalised word ('IdP00004'). Any other continuation is another word
    # that merely begins like the label ('Sex' in 'Sexual history').
    raw = " ".join(re.sub(r"[:*]", " ", text or "").split())
    if raw.lower().startswith(ln) and len(raw) > len(ln):
        last, nxt = raw[len(ln) - 1], raw[len(ln)]
        return nxt.isdigit() or (nxt.isupper() and last.islower())
    return False


def _label_matches(label: str, text: str) -> bool:
    """Is `text` the label `label`? Case, spacing, ':' and '*' are ignored.

    Yes if the two are equal, if the text contains the label as whole words
    ('First Name *', or a label read together with its value), or if they
    differ only by characters OCR mistakes for one another ('Date 0f Birth').
    Nothing looser: a similarity threshold took 'Place Of Birth' for 'Date Of
    Birth', the date went into the wrong field and the check passed; and a
    text merely contained IN the label let the calendar cells '10', '1' and
    '20' pass for the label '10/10/2000'. A label OCR has garbled beyond that
    is not found, and the planner, who reads the same garbled text in the
    element list, has to name it as it is shown."""
    def norm(t: str) -> str:
        return " ".join(re.sub(r"[:*]", " ", (t or "").lower()).split())
    want, have = norm(label), norm(text)
    if not want or not have:
        return False
    return want == have or f" {want} " in f" {have} " or typed_matches(want, have)[0]


def _find_label_elements(label: str, elements: List[Element]) -> List[Element]:
    """OCR elements whose text is the label `label` (see _label_matches). A
    text that IS the label beats texts that only contain it: next to the label
    'Prescription' there is often a 'Prescription Date'. And a text that is
    the label as written, marks included, beats one that is equal only
    without them: a form's 'Medication *' is not the menu entry 'Medication'."""
    found = [el for el in elements
             if el.source != "yolo" and _label_matches(label, el.text)]

    def squeezed(text: str) -> str:
        return "".join((text or "").split()).casefold()
    verbatim = [el for el in found if squeezed(el.text) == squeezed(label)]
    if verbatim:
        return verbatim
    want = _norm_text(label)
    same = [el for el in found
            if _norm_text(el.text) == want or typed_matches(want, _norm_text(el.text))[0]]
    return same or found


def _same_row(a: Element, b: Element) -> bool:
    """Two boxes sit on one row when their vertical extents overlap by at
    least half the height of the smaller one. (A tolerance on the centres,
    scaled by the box height, took a 46 px input for being on the row of a
    label that lay entirely below it — and the value went into that input.)"""
    overlap = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    return overlap >= 0.5 * min(a.height, b.height)


def _is_value_adjacent_to_label(value_el: Element, label_el: Element) -> bool:
    """Geometric adjacency for form layouts: value on the same row to the right
    of the label, or directly below it."""
    if value_el is label_el:
        return False
    # Same row, value to the right of the label.
    if _same_row(value_el, label_el):
        gap = value_el.x - (label_el.x + label_el.width)
        if -10 <= gap <= 400:
            return True
    # Value directly below the label (label-above-field layout). A detected
    # control's box may reach a few pixels into the label's.
    vgap = value_el.y - (label_el.y + label_el.height)
    if value_el.y >= label_el.center_y and vgap <= 70:
        overlap = (min(value_el.x + value_el.width, label_el.x + label_el.width)
                   - max(value_el.x, label_el.x))
        if overlap > 0 or abs(value_el.x - label_el.x) <= 40:
            return True
    return False


def _nearest_label(el: Element, elements: List[Element]) -> Optional[Element]:
    """The label element `el` most plausibly belongs to (left on the same row,
    or directly above), or None."""
    best, best_d = None, float("inf")
    for cand in elements:
        if cand is el or cand.source == "yolo":
            continue
        text = (cand.text or "").strip()
        if not text or not any(ch.isalpha() for ch in text):
            continue
        if _is_value_adjacent_to_label(el, cand):
            d = abs(el.center_y - cand.center_y) + max(0, el.x - (cand.x + cand.width))
            if d < best_d:
                best, best_d = cand, d
    return best


def check_value_near_label(
    value: str, label: str, elements: List[Element]
) -> Tuple[str, str]:
    """Label-anchored evidence check. Returns (verdict, detail):
      'pass'     — the value is on screen adjacent to (or merged with) the label.
      'fail'     — the value is missing, or present only next to OTHER labels.
      'no_label' — the expected label is not on screen (fall back to presence)."""
    value_els = [el for el in elements
                 if el.source != "yolo" and _value_in_text(value, el.text)]
    # Same-element case first: OCR often merges 'Label: value' into one box,
    # and box-to-box adjacency can never match an element against itself.
    for v_el in value_els:
        if _label_in_text(label, v_el.text):
            return "pass", f"'{v_el.text.strip()}' contains both '{label}' and the value"
    label_els = _find_label_elements(label, elements)
    if not label_els:
        return "no_label", f"label '{label}' not found on screen (OCR may have missed it)"
    if not value_els:
        return "fail", f"value '{value}' is not visible anywhere on screen"
    for v_el in value_els:
        for l_el in label_els:
            if _is_value_adjacent_to_label(v_el, l_el):
                return "pass", (
                    f"'{v_el.text.strip()}' found next to '{l_el.text.strip()}'"
                )
    near = _nearest_label(value_els[0], elements)
    where = f" (it appears next to '{near.text.strip()}')" if near else ""
    return "fail", f"value '{value}' is on screen but NOT next to '{label}'{where}"


# Characters OCR reads for one another. A typed value may differ from what the
# field is read to show ONLY by such a pair; any other difference is real.
_OCR_LOOKALIKES = ("l1i|!", "o0", "s5", "b8", "z2", "g9", ".,", "'`", "-_")


def typed_matches(value: str, shown: str) -> Tuple[bool, str]:
    """Does a field show what was typed? Returns (same, difference).

    Spacing and case are ignored, and a character OCR is known to mistake for
    another (same position, see _OCR_LOOKALIKES) is let through. A character
    that is missing, added or replaced by anything else is a difference — the
    looser word-overlap test that FINDS the value on screen once passed
    'jane.doeexample.com' for 'jane.doe@example.com'."""
    v = "".join((value or "").split()).casefold()
    t = "".join((shown or "").split()).casefold()
    if v == t:
        return True, ""
    missing: List[str] = []
    extra: List[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, v, t, autojunk=False).get_opcodes():
        if op == "delete":
            missing.append(v[i1:i2])
        elif op == "insert":
            extra.append(t[j1:j2])
        elif op == "replace":
            if i2 - i1 == j2 - j1 and all(
                    any(a in grp and b in grp for grp in _OCR_LOOKALIKES)
                    for a, b in zip(v[i1:i2], t[j1:j2])):
                continue   # read differently, not typed differently
            missing.append(v[i1:i2])
            extra.append(t[j1:j2])
    if not missing and not extra:
        return True, ""
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(f"'{m}'" for m in missing))
    if extra:
        parts.append("not typed: " + ", ".join(f"'{e}'" for e in extra))
    return False, "; ".join(parts)


def _cut_label(label: str, text: str) -> str:
    """`text` without a leading `label` ('Middle Name: A' → 'A'): OCR often
    reads a label and its field's value as one box."""
    text = (text or "").strip()
    cut = re.match(r"\s*" + r"\s*".join(re.escape(w) for w in label.split())
                   + r"\s*[:*]?\s*", text, flags=re.IGNORECASE)
    return text[cut.end():] if cut else text


def shown_value(value: str, label: str, elements: List[Element]
                ) -> Optional[Tuple[str, Element]]:
    """The text a field shows for `label`, and its element: the element that
    check_value_near_label accepted as holding the value. A label merged into
    the same OCR box ('Middle Name A') is cut off. None if there is none."""
    value_els = [el for el in elements
                 if el.source != "yolo" and _value_in_text(value, el.text)]
    for v_el in value_els:
        if _label_in_text(label, v_el.text):
            return _cut_label(label, v_el.text), v_el
    label_els = _find_label_elements(label, elements)
    for v_el in value_els:
        if any(_is_value_adjacent_to_label(v_el, l_el) for l_el in label_els):
            return v_el.text.strip(), v_el
    return None


def describe_caret_position(
    caret_xy: Tuple[int, int], elements: List[Element]
) -> str:
    """Resolve WHICH field a blinking caret sits in — pure geometry, no LLM.

    Returns e.g. "inside [e81] 'John', next to label 'First Name *'" (either
    part may be absent), or "" when nothing can be resolved. Saves the actioner
    from inferring the focused field from a bare coordinate — and exposes
    focus landing in the WRONG field immediately."""
    x, y = caret_xy
    containing = [el for el in elements
                  if el.x <= x <= el.x + el.width and el.y <= y <= el.y + el.height]
    best = min(containing, key=lambda el: el.width * el.height) if containing else None
    label = caret_label(caret_xy, elements)
    parts: List[str] = []
    if best is not None:
        text = best.text.replace("\n", " ").strip()
        desc = f"[{best.stable_id}]" + (f" '{text[:40]}'" if text else " <interactive>")
        parts.append(f"inside {desc}")
    if label is not None and label is not best:
        parts.append(f"next to label '{label.text.strip()[:40]}'")
    return ", ".join(parts)


def guard_wrong_field(
    change: "ChangeSet", todo: "Step", elements: List[Element]
) -> Optional[str]:
    """Deterministic wrong-field detector, run after every action.

    If the current step expects `value` next to `label` and the change-set shows
    that value materializing ONLY next to DIFFERENT labels, return an alarm
    string; else None. Pure geometry — no LLM call.

    Echo suppression: applications commonly mirror an edited value elsewhere on
    the same screen (page title, breadcrumb, list row — e.g. HospitalRun shows
    'John Doe-Doe' in the header the moment the Last Name field is edited).
    So the alarm fires only when the value landed EXCLUSIVELY in wrong places:
    one hit next to the expected label proves the edit itself was correct, and
    the other occurrences are echoes, not mistakes."""
    if todo is None or not todo.expected_value or not todo.expected_label:
        return None
    # Scan text_changed AND appeared: typing into an EMPTY field registers as
    # 'appeared' (there was no old element to pair with), not 'text_changed' —
    # without this, the correct-field hit is invisible and an echo elsewhere
    # (page title) raises a false alarm.
    mutations = change.text_changed + change.appeared
    if not mutations:
        return None
    expected_labels = _find_label_elements(todo.expected_label, elements)
    if not expected_labels:
        # The expected label is not OCR-visible — geometry cannot distinguish
        # right from wrong field, so do not alarm (avoid false positives).
        return None
    wrong_hits: List[Element] = []
    for ch in mutations:
        el = ch.element
        if not _value_in_text(todo.expected_value, el.text):
            continue
        if (_label_in_text(todo.expected_label, el.text)
                or any(_is_value_adjacent_to_label(el, l_el) for l_el in expected_labels)):
            return None  # landed in the right field — other occurrences are echoes
        wrong_hits.append(el)
    if not wrong_hits:
        return None
    el = wrong_hits[0]
    near = _nearest_label(el, elements)
    near_txt = f"'{near.text.strip()}'" if near else "an unidentified field"
    return (
        f"the value '{todo.expected_value}' just appeared next to {near_txt}, "
        f"but it was expected next to '{todo.expected_label}': it was typed "
        f"into the wrong field"
    )


def _norm_text(s: str) -> str:
    """Comparison form of a label or control text: case, ':' / '*' and spacing ignored."""
    return " ".join(re.sub(r"[:*]", " ", (s or "").lower()).split())


def caret_label(caret_xy: Tuple[int, int], elements: List[Element]) -> Optional[Element]:
    """The label of the field the blinking caret sits in, or None — pure geometry.

    In a filled field the caret abuts the field's own text, which would itself
    pass for the nearest "label"; so the label is looked up from the text
    element holding the caret when there is one, else from the caret position."""
    x, y = caret_xy
    holders = [el for el in elements
               if el.source != "yolo"
               and el.x - 8 <= x <= el.x + el.width + 8 and el.y <= y <= el.y + el.height]
    if not holders:   # an empty field: the detected control around the caret
        holders = [el for el in elements
                   if el.source == "yolo"
                   and el.x <= x <= el.x + el.width and el.y <= y <= el.y + el.height]
    if holders:
        anchor = min(holders, key=lambda el: el.width * el.height)
    else:
        anchor = Element(
            stable_id="", text="", control_type="Caret", source="ocr",
            x=x - 1, y=y - 8, width=2, height=16, center_x=x, center_y=y,
        )
    return _nearest_label(anchor, elements)
