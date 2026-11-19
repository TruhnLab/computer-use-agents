"""Reading a picture: RapidOCR for text, the OmniParser YOLO detector for controls
without text."""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

from .elements import Element
from .paths import ROOT

log = logging.getLogger("agent")


# RapidOCR engines — created on first use so import-time cost is zero.
_rapidocr_engine: Optional[RapidOCR] = None


_rapidocr_crop_engine: Optional[RapidOCR] = None


_RAPIDOCR_PARAMS = {
    "Rec.lang_type": LangRec.LATIN,
    "Rec.model_type": ModelType.MOBILE,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Det.model_type": ModelType.MOBILE,
    # No orientation classifier: it turns a text crop by 180° when it judges
    # the text upside down, and it judged so wrongly for small button labels
    # ('Edit' was read as 'B!P3' and dropped). Text in a UI is upright.
    "Global.use_cls": False,
    "Global.log_level": "error",
}


def _get_rapidocr_engine() -> RapidOCR:
    global _rapidocr_engine
    if _rapidocr_engine is None:
        _rapidocr_engine = RapidOCR(params=dict(_RAPIDOCR_PARAMS))
    return _rapidocr_engine


def _get_rapidocr_crop_engine() -> RapidOCR:
    """Engine for crops of a window (partial parse). The default detector
    enlarges an image until its short side is 736px: a field-sized crop is
    blown up many times, which costs seconds and reads text differently from
    the full frame ('First Name *' lost its asterisk). This engine keeps crops
    at their native scale, the scale a full-window parse reads them at."""
    global _rapidocr_crop_engine
    if _rapidocr_crop_engine is None:
        _rapidocr_crop_engine = RapidOCR(params={
            **_RAPIDOCR_PARAMS, "Det.limit_side_len": 32, "Det.limit_type": "min",
        })
    return _rapidocr_crop_engine


# YOLO icon-detector engine (OmniParser v2) — lazy-loaded on first use. -----
# First call: checks models/icon_detect.onnx (put there by download_models.py);
# if absent, falls back to downloading + exporting from HuggingFace Hub.

_YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", "1920"))


_YOLO_CONF_THRESH = float(os.getenv("YOLO_CONF_THRESH", "0.05"))


_YOLO_IOU_THRESH = float(os.getenv("YOLO_IOU_THRESH", "0.3"))


# Fraction of an OCR box that must be covered by a YOLO box for the text to
# count as sitting INSIDE that control (a filled edit field, a labeled button).
# Such a text stands for the control: it inherits the 'interactive' attribute
# and the YOLO box is dropped as redundant. A text below this only grazes the
# box — it must neither be tagged interactive nor make the control disappear.
# (A lower, separate drop threshold of 5% once did the latter: the label of the
# field BELOW, overlapping an empty input's border by 3px, removed that input
# from the element list.)
_YOLO_TEXT_FUSE_THRESH = float(os.getenv("YOLO_TEXT_FUSE_THRESH", "0.5"))


# Local model directory — download_models.py writes here.
_MODELS_DIR = ROOT / "models"


_LOCAL_ONNX = _MODELS_DIR / "icon_detect.onnx"


_yolo_session: Any = None       # onnxruntime.InferenceSession | None | _YOLO_FAILED


_yolo_input_name: str = "images"


_YOLO_FAILED = object()         # sentinel: init attempted and failed — do not retry


# Pillow 9.1+ uses Image.Resampling; Pillow <9.1 used Image.BILINEAR directly.
_BILINEAR = getattr(getattr(Image, "Resampling", None), "BILINEAR", None) or Image.BILINEAR


def _get_yolo_session():
    """Return a cached ORT InferenceSession for the YOLO icon detector.

    Resolution order:
    1. models/icon_detect.onnx (put there by download_models.py) — preferred.
    2. ONNX next to the HF-cached .pt (exported on first run if missing).
    Returns None if setup failed (will not retry).
    """
    global _yolo_session, _yolo_input_name
    if _yolo_session is _YOLO_FAILED:
        return None
    if _yolo_session is not None:
        return _yolo_session
    _log = logging.getLogger("agent")
    try:
        import onnxruntime as ort

        def _load_session(onnx_path: Path) -> "ort.InferenceSession":
            so = ort.SessionOptions()
            so.intra_op_num_threads = 4
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            return ort.InferenceSession(
                str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
            )

        def _export_from_hub() -> Path:
            from huggingface_hub import hf_hub_download
            pt_path = hf_hub_download(
                repo_id="microsoft/OmniParser-v2.0",
                filename="icon_detect/model.pt",
            )
            onnx_path = Path(pt_path).with_suffix(".onnx")
            if not onnx_path.exists():
                _log.info("Exporting YOLO icon detector to ONNX (one-time) ...")
                from ultralytics import YOLO as _YOLO
                exported = _YOLO(pt_path).export(
                    format="onnx", imgsz=_YOLO_IMGSZ, simplify=True, dynamic=True
                )
                onnx_path = Path(exported)
                _log.info("ONNX export complete: %s", onnx_path)
            return onnx_path

        # 1. Prefer local models/ directory.
        if _LOCAL_ONNX.exists():
            onnx_path = _LOCAL_ONNX
            _log.info("Loading local ONNX: %s", onnx_path)
        else:
            _log.info(
                "models/icon_detect.onnx not found — falling back to HF Hub download."
                " Run download_models.py once to avoid this."
            )
            onnx_path = _export_from_hub()

        try:
            sess = _load_session(onnx_path)
        except Exception as load_err:
            # Possibly a corrupted ONNX — delete and re-export from Hub.
            _log.warning("ORT failed to load %s (%s); re-exporting ...", onnx_path.name, load_err)
            onnx_path.unlink(missing_ok=True)
            onnx_path = _export_from_hub()
            sess = _load_session(onnx_path)

        _yolo_input_name = sess.get_inputs()[0].name
        _yolo_session = sess
        _log.info(
            "YOLO icon detector ready (imgsz=%d, input=%r): %s",
            _YOLO_IMGSZ, _yolo_input_name, onnx_path.name,
        )
    except Exception as exc:
        _log.error(
            "YOLO icon detector init failed (%s: %s) — icon detection disabled.",
            type(exc).__name__, exc, exc_info=True,
        )
        _yolo_session = _YOLO_FAILED
    return _yolo_session if _yolo_session is not _YOLO_FAILED else None


def _letterbox(
    image: "Image.Image", size: int
) -> "Tuple[np.ndarray, float, int, int]":
    """Resize *image* to a (size x size) square with grey letterbox padding.

    Returns (inp, scale, pad_top, pad_left) where *inp* is a float32 ndarray
    of shape (1, 3, size, size) ready for ORT inference.
    """
    w, h = image.size
    scale = size / max(h, w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = image.resize((nw, nh), _BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad_left = (size - nw) // 2
    pad_top = (size - nh) // 2
    canvas.paste(resized, (pad_left, pad_top))
    arr = np.array(canvas, dtype=np.float32) / 255.0
    arr = arr.transpose(2, 0, 1)[np.newaxis]   # (1, 3, H, W)
    return arr, scale, pad_top, pad_left


def _letterbox_at_scale(image: "Image.Image", scale: float) -> "np.ndarray":
    """Resize *image* by a GIVEN scale onto a grey canvas whose sides are the
    next multiples of 32 (the detector's stride), pasted top-left.

    For crops: a crop letterboxed to the full input size would be magnified and
    its controls would no longer look like they do in a full-window parse. At
    the window's own scale they do, and the inference costs only the crop."""
    w, h = image.size
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = image.resize((nw, nh), _BILINEAR)
    canvas = Image.new("RGB", (max(64, -(-nw // 32) * 32), max(64, -(-nh // 32) * 32)),
                       (114, 114, 114))
    canvas.paste(resized, (0, 0))
    arr = np.array(canvas, dtype=np.float32) / 255.0
    return arr.transpose(2, 0, 1)[np.newaxis]   # (1, 3, H, W)


def _nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thresh: float) -> List[int]:
    """Greedy IoU-based NMS. Returns indices of boxes to keep (highest score first)."""
    order = scores.argsort()[::-1]
    kept: List[int] = []
    while order.size > 0:
        i = int(order[0])
        kept.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(boxes_xyxy[i, 0], boxes_xyxy[order[1:], 0])
        yy1 = np.maximum(boxes_xyxy[i, 1], boxes_xyxy[order[1:], 1])
        xx2 = np.minimum(boxes_xyxy[i, 2], boxes_xyxy[order[1:], 2])
        yy2 = np.minimum(boxes_xyxy[i, 3], boxes_xyxy[order[1:], 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_i = (boxes_xyxy[i, 2] - boxes_xyxy[i, 0]) * (boxes_xyxy[i, 3] - boxes_xyxy[i, 1])
        area_j = ((boxes_xyxy[order[1:], 2] - boxes_xyxy[order[1:], 0])
                  * (boxes_xyxy[order[1:], 3] - boxes_xyxy[order[1:], 1]))
        iou = inter / (area_i + area_j - inter + 1e-6)
        order = order[1:][iou <= iou_thresh]
    return kept


# --- OCR ------------------------------------------------------------------- #

def _rapidocr_to_boxes(result, min_score: float = 0.8) -> List[Dict[str, Any]]:
    """Convert a RapidOCR result (polygon-based) to axis-aligned box dicts.

    Only detections with a recognition confidence *score* > *min_score* are
    returned.
    """
    if result.boxes is None:
        return []
    boxes: List[Dict[str, Any]] = []
    for poly, text, score in zip(result.boxes, result.txts, result.scores):
        if score <= min_score:
            continue
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x, y = int(min(xs)), int(min(ys))
        x2, y2 = int(max(xs)), int(max(ys))
        boxes.append({
            "text": text,
            "conf": int(score * 100),
            "x": x, "y": y,
            "w": x2 - x, "h": y2 - y,
            "cx": (x + x2) // 2,
            "cy": (y + y2) // 2,
        })
    return boxes


def _iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Intersection-over-union for two axis-aligned boxes dict('x','y','w','h')."""
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["w"], by1 + b["h"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _coverage(yolo: Dict[str, Any], ocr: Dict[str, Any]) -> float:
    """Fraction of the *ocr* box's area that is covered by the *yolo* box.

    Using the OCR box as reference catches large YOLO boxes (e.g. an edit
    field) that contain a small text label inside them: the OCR box is almost
    entirely overlapped even though the YOLO box itself is much bigger.
    """
    ix1 = max(yolo["x"], ocr["x"])
    iy1 = max(yolo["y"], ocr["y"])
    ix2 = min(yolo["x"] + yolo["w"], ocr["x"] + ocr["w"])
    iy2 = min(yolo["y"] + yolo["h"], ocr["y"] + ocr["h"])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    ocr_area = ocr["w"] * ocr["h"]
    return inter / ocr_area if ocr_area > 0 else 0.0


def extract_ocr_elements(image: Image.Image, crop: bool = False) -> List[Element]:
    """Run RapidOCR on *image* and return a de-duplicated Element list.
    *crop* marks a crop of a window, read at its native scale."""
    engine = _get_rapidocr_crop_engine() if crop else _get_rapidocr_engine()
    try:
        result = engine(np.array(image.convert("RGB")))
    except Exception:
        log.exception("RapidOCR failed")
        return []

    all_boxes = _rapidocr_to_boxes(result)

    # IoU-based dedup (safety net — RapidOCR rarely produces overlapping boxes).
    all_boxes.sort(key=lambda b: -b["conf"])
    kept: List[Dict[str, Any]] = []
    for box in all_boxes:
        is_dup = False
        for k in kept:
            if _iou(box, k) < 0.5:
                continue
            if difflib.SequenceMatcher(None, box["text"].lower(), k["text"].lower()).ratio() >= 0.6:
                is_dup = True
                break
        if not is_dup:
            kept.append(box)

    return [
        Element(
            stable_id="",
            text=b["text"], control_type="Text", source="ocr",
            x=b["x"], y=b["y"], width=b["w"], height=b["h"],
            center_x=b["cx"], center_y=b["cy"],
            confidence=b["conf"],
        )
        for b in kept
    ]


def extract_icon_elements(image: "Image.Image", scale: Optional[float] = None) -> List[Element]:
    """Run the OmniParser YOLO icon detector on *image*.

    Returns a list of Elements with source="yolo" and empty text, representing
    visually detected interactive regions (buttons, icons, empty fields).
    Only boxes that survive NMS and meet the confidence threshold are returned;
    overlapping with OCR boxes is handled by the caller.

    *scale* is given for a crop of a window: the crop is then run at that pixel
    scale (the one the full window gets) instead of being fitted to the full
    input size.
    """
    session = _get_yolo_session()
    if session is None:
        return []
    try:
        if scale is None:
            inp, scale, pad_top, pad_left = _letterbox(image.convert("RGB"), _YOLO_IMGSZ)
        else:
            inp, pad_top, pad_left = _letterbox_at_scale(image.convert("RGB"), scale), 0, 0
        raw = session.run(None, {_yolo_input_name: inp})[0]  # (1, 5, N) or (1, N, 5)
        preds = raw[0]  # (5, N) or (N, 5)
        if preds.shape[0] < preds.shape[1]:  # (5, N) → transpose to (N, 5)
            preds = preds.T
        # columns: cx, cy, w, h, conf  (single-class model)
        scores = preds[:, 4]
        mask = scores > _YOLO_CONF_THRESH
        preds, scores = preds[mask], scores[mask]
        if len(preds) == 0:
            return []
        # Convert from IMGSZ space to original image pixel coords
        cx, cy, bw, bh = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
        x1 = (cx - bw / 2 - pad_left) / scale
        y1 = (cy - bh / 2 - pad_top) / scale
        x2 = (cx + bw / 2 - pad_left) / scale
        y2 = (cy + bh / 2 - pad_top) / scale
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)
        keep = _nms(boxes_xyxy, scores, _YOLO_IOU_THRESH)
        W, H = image.size
        elements: List[Element] = []
        for idx in keep:
            xi1 = max(0, int(boxes_xyxy[idx, 0]))
            yi1 = max(0, int(boxes_xyxy[idx, 1]))
            xi2 = min(W, int(boxes_xyxy[idx, 2]))
            yi2 = min(H, int(boxes_xyxy[idx, 3]))
            if xi2 <= xi1 or yi2 <= yi1:
                continue
            elements.append(Element(
                stable_id="",
                text="", control_type="Icon", source="yolo",
                x=xi1, y=yi1, width=xi2 - xi1, height=yi2 - yi1,
                center_x=(xi1 + xi2) // 2, center_y=(yi1 + yi2) // 2,
                confidence=int(scores[idx] * 100),
            ))
        return elements
    except Exception as exc:
        log.error("YOLO icon detection failed: %s: %s", type(exc).__name__, exc)
        return []
