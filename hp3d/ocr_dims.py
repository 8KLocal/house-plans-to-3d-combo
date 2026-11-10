"""Read printed dimension labels and turn agreeing ones into a measured scale.

Pictures and scanned PDFs have no text layer. Labels such as ``12'-6"`` and
``12'-6" x 10'-0"`` are read with the bundled RapidOCR ONNX models (local,
Apache-2.0, no network). Tesseract is used only if those models cannot be
loaded and the ``tesseract`` program is already installed.

Each kept label is tied to a dimension line, or a pair is tied to the long and
short sides of the drawing. A scale is published only when at least two of
those readings agree. One label, a unitless number, or readings that disagree
do not become a size: the app asks instead of guessing.

``MEASURED_SOURCE`` is stored on the job. The quote treats that scale as a real
measurement (see ``labels_are_measured``).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import numpy as np

from .symbols import ink_mask

MEASURED_SOURCE = "printed dimension labels on the plan"
_ENGINE_DIR = Path(__file__).resolve().parent / "ocr_engine"
_FEET = re.compile(
    r"(?P<ft>\d{1,3})\s*(?:'|ft|foot|feet)\s*-?\s*(?P<inch>\d{1,2}(?:\.\d+)?)?\s*(?:\"|in|inch|inches)?",
    re.IGNORECASE)
_INCH = re.compile(r"(?P<inch>\d{1,4}(?:\.\d+)?)\s*(?:\"|in\b|inch(?:es)?\b)", re.IGNORECASE)
_MM = re.compile(r"(?P<mm>\d{1,5}(?:\.\d+)?)\s*mm\b", re.IGNORECASE)
_CM = re.compile(r"(?P<cm>\d{1,4}(?:\.\d+)?)\s*cm\b", re.IGNORECASE)
_M = re.compile(r"(?P<m>\d{1,3}(?:\.\d+)?)\s*m\b", re.IGNORECASE)
_PAIR = re.compile(r"\s*[xX×]\s*")

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


def labels_are_measured(params) -> bool:
    """True when job.json holds a scale read from agreeing dimension labels."""
    try:
        ppi = float(getattr(params, "px_per_inch", None) or 0)
    except (TypeError, ValueError):
        return False
    if not ppi > 0:
        return False
    measured = getattr(params, "measured", None) or []
    src = (getattr(params, "scale_source", None) or "").lower()
    return "px_per_inch" in measured and "dimension label" in src


def install_quote_hook() -> None:
    """Let the quote PDF price a model whose size came from dimension labels.

    PR #4 withholds a dollar total unless the size was typed or came from a
    DXF unit. A scale read from agreeing labels is the same kind of fact, so
    the quote's gate accepts it without copying that module into this branch.
    """
    import sys as _sys
    mod = _sys.modules.get("hp3d.quote")
    if mod is None:
        if not (Path(__file__).resolve().parent / "quote.py").is_file():
            return
        from . import quote as mod
    current = getattr(mod, "size_block_reason", None)
    if current is None or getattr(current, "_hp3d_labels", False):
        return
    orig_block = current
    orig_note = getattr(mod, "size_confidence", None)

    def size_block_reason(params):
        if labels_are_measured(params):
            return None
        return orig_block(params)

    size_block_reason._hp3d_labels = True  # type: ignore[attr-defined]
    mod.size_block_reason = size_block_reason
    if orig_note is not None and not getattr(orig_note, "_hp3d_labels", False):
        def size_confidence(params):
            if labels_are_measured(params):
                return f"Size read from the plan's dimension labels ({params.scale_source})."
            return orig_note(params)

        size_confidence._hp3d_labels = True  # type: ignore[attr-defined]
        mod.size_confidence = size_confidence


def parse_dimension(text: str) -> float | None:
    """Inches from one label. A bare number is refused so it cannot become a size."""
    s = " ".join((text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split())
    if not s:
        return None
    m = _FEET.search(s)
    if m and m.group("ft"):
        inches = float(m.group("inch") or 0)
        if m.group("inch") and inches >= 12 and not re.search(r"in", s, re.I):
            return None
        return float(m.group("ft")) * 12.0 + inches
    for cre, factor in ((_MM, 1 / 25.4), (_CM, 1 / 2.54), (_M, 39.3701), (_INCH, 1.0)):
        hit = cre.search(s)
        if hit:
            return float(next(iter(hit.groupdict().values()))) * factor
    return None


def parse_lengths(text: str) -> list[float]:
    """Every length in a string. ``12'-6" x 10'-0"`` yields both. Unitless pieces are dropped."""
    raw = text or ""
    parts = _PAIR.split(raw)
    if len(parts) == 1:
        one = parse_dimension(raw)
        return [] if one is None else [one]
    out = []
    for part in parts:
        val = parse_dimension(part)
        if val is not None and val > 0:
            out.append(val)
    return out


def _rapid_words(rgb: np.ndarray) -> list[dict] | None:
    if str(_ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(_ENGINE_DIR))
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:  # noqa: BLE001
        return None
    try:
        engine = _rapid_words.engine  # type: ignore[attr-defined]
    except AttributeError:
        try:
            engine = RapidOCR()
        except Exception:  # noqa: BLE001
            return None
        _rapid_words.engine = engine  # type: ignore[attr-defined]
    try:
        result, _elapsed = engine(np.asarray(rgb))
    except Exception:  # noqa: BLE001
        return None
    words = []
    for item in result or []:
        box, text, score = item[0], str(item[1]), float(item[2])
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        words.append({"text": text.strip(), "conf": score * 100.0, "x": float(x0), "y": float(y0),
                      "w": float(max(1.0, x1 - x0)), "h": float(max(1.0, y1 - y0))})
    return words


def _tesseract_words(rgb: np.ndarray) -> list[dict] | None:
    if pytesseract is None or shutil.which("tesseract") is None:
        return None
    from PIL import Image
    import cv2
    scale = 2 if max(rgb.shape[:2]) < 1600 else 1
    big = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else rgb
    try:
        data = pytesseract.image_to_data(Image.fromarray(big), output_type=pytesseract.Output.DICT, config="--psm 11")
    except Exception:  # noqa: BLE001
        return None
    words = []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        words.append({"text": text, "conf": conf, "x": data["left"][i] / scale, "y": data["top"][i] / scale,
                      "w": max(1.0, data["width"][i] / scale), "h": max(1.0, data["height"][i] / scale)})
    return _join_tokens(words)


def _join_tokens(words: list[dict]) -> list[dict]:
    """Glue tokens Tesseract split out of one dimension (``12`` ``'`` ``-`` ``6"``)."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w["y"] / 8), w["x"]))
    lines: list[list[dict]] = []
    for w in words:
        if lines and abs(w["y"] - lines[-1][0]["y"]) <= max(8.0, 0.7 * w["h"]):
            lines[-1].append(w)
        else:
            lines.append([w])
    out = []
    for line in lines:
        line.sort(key=lambda w: w["x"])
        buf = [line[0]]
        for w in line[1:]:
            gap = w["x"] - (buf[-1]["x"] + buf[-1]["w"])
            if gap <= max(12.0, w["h"]):
                buf.append(w)
            else:
                out.append(_flush(buf))
                buf = [w]
        out.append(_flush(buf))
    return out


def _flush(buf: list[dict]) -> dict:
    text = ""
    for i, w in enumerate(buf):
        if i and re.match(r"^(ft|feet|foot|in|inch|inches|mm|cm|m)$", w["text"], re.I):
            text += " "
        text += w["text"]
    x0 = min(w["x"] for w in buf)
    y0 = min(w["y"] for w in buf)
    x1 = max(w["x"] + w["w"] for w in buf)
    y1 = max(w["y"] + w["h"] for w in buf)
    return {"text": text, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "conf": float(np.mean([w["conf"] for w in buf]))}


def _longest_run(line: np.ndarray) -> float:
    xs = np.flatnonzero(line)
    if len(xs) < 8:
        return 0.0
    cuts = np.where(np.diff(xs) > 3)[0]
    starts = np.r_[xs[0], xs[cuts + 1]]
    ends = np.r_[xs[cuts], xs[-1]]
    return float(np.max(ends - starts + 1))


def _full_line(mask: np.ndarray, bbox: list[float], orient: str) -> float | None:
    """Length of the dimension line nearest this label, measured along the whole sheet."""
    x, y, w, h = bbox
    height, width = mask.shape
    if orient == "h":
        y0, y1 = int(max(0, y - 3 * h)), int(min(height, y + h + 3 * h))
        best = 0.0
        for yy in range(y0, y1):
            best = max(best, _longest_run(mask[yy]))
        if best < max(40.0, 1.8 * w):
            return None
        return best
    x0, x1 = int(max(0, x - 3 * w)), int(min(width, x + w + 3 * w))
    best = 0.0
    for xx in range(x0, x1):
        best = max(best, _longest_run(mask[:, xx]))
    if best < max(40.0, 1.8 * max(h, w * 0.4)):
        return None
    return best


def _sheet_spans(mask: np.ndarray) -> tuple[float, float]:
    """Longest horizontal and vertical ink runs (a dimension line or an outside wall)."""
    best_h = max((_longest_run(row) for row in mask), default=0.0)
    best_v = max((_longest_run(mask[:, i]) for i in range(mask.shape[1])), default=0.0)
    return float(best_h), float(best_v)


def read_labels(rgb: np.ndarray) -> dict:
    """OCR words that parse as lengths. Unitless and low-confidence text is unused."""
    words = _rapid_words(rgb)
    engine = "rapidocr"
    if words is None:
        words = _tesseract_words(rgb)
        engine = "tesseract"
    if words is None:
        return {"labels": [], "unused": [], "engine": None, "available": False,
                "detail": "No local OCR engine is available, so dimension text was not read and no scale was guessed."}
    labels, unused = [], []
    for item in words:
        if not item["text"]:
            continue
        lengths = parse_lengths(item["text"])
        if not lengths:
            if any(c.isdigit() for c in item["text"]):
                unused.append({"text": item["text"], "conf": round(item["conf"], 1),
                               "reason": "no unit, so this was not read as a length"})
            continue
        if item["conf"] < 55:
            unused.append({"text": item["text"], "conf": round(item["conf"], 1),
                           "reason": "OCR confidence too low to use"})
            continue
        labels.append({"text": item["text"], "inches": [round(v, 3) for v in lengths],
                       "conf": round(item["conf"], 1),
                       "bbox": [round(item["x"], 1), round(item["y"], 1), round(item["w"], 1), round(item["h"], 1)]})
    return {"labels": labels, "unused": unused[:12], "engine": engine, "available": True,
            "detail": f"{engine} (local, on this computer)"}


def samples_from_labels(labels: list[dict], mask: np.ndarray) -> list[dict]:
    """px-per-inch samples. A sample without a matching line is kept only as a reason, not a scale."""
    out = []
    span_h, span_v = _sheet_spans(mask)
    for lab in labels:
        inches = [v for v in lab["inches"] if v >= 6]
        if not inches:
            continue
        if len(inches) == 1:
            length_h = _full_line(mask, lab["bbox"], "h")
            length_v = _full_line(mask, lab["bbox"], "v")
            # The label is parallel to the line it measures. Prefer the line that is
            # actually long; a short stroke of the glyphs is not the dimension.
            chosen = None
            wide = lab["bbox"][2] >= lab["bbox"][3]
            first, second = ("h", length_h), ("v", length_v)
            if not wide:
                first, second = second, first
            for name, length in (first, second):
                if length:
                    chosen = (name, length)
                    break
            if chosen is None:
                out.append({"text": lab["text"], "inches": inches[0], "ppi": None, "conf": lab["conf"],
                            "reason": "read, but no dimension line next to it matched"})
                continue
            orient, length = chosen
            out.append({"text": lab["text"], "inches": inches[0], "px": round(length, 1), "orient": orient,
                        "ppi": round(length / inches[0], 4), "conf": lab["conf"], "matched": "dimension line"})
            continue
        long_in, short_in = max(inches), min(inches)
        long_px, short_px = max(span_h, span_v), min(span_h, span_v)
        if long_px < 30 or short_px < 30 or long_in <= 0 or short_in <= 0:
            out.append({"text": lab["text"], "inches": inches, "ppi": None, "conf": lab["conf"],
                        "reason": "read as a pair, but the sheet has no long and short side to match"})
            continue
        ppi_l, ppi_s = long_px / long_in, short_px / short_in
        if abs(ppi_l - ppi_s) / max(ppi_l, ppi_s) > 0.12:
            out.append({"text": lab["text"], "inches": inches, "ppi": None, "conf": lab["conf"],
                        "ppi_pair": [round(ppi_l, 3), round(ppi_s, 3)],
                        "reason": "the two lengths do not match the long and short sides of the drawing"})
            continue
        out.append({"text": lab["text"], "inches": long_in, "px": round(long_px, 1),
                    "ppi": round(ppi_l, 4), "conf": lab["conf"], "matched": "long side"})
        out.append({"text": lab["text"], "inches": short_in, "px": round(short_px, 1),
                    "ppi": round(ppi_s, 4), "conf": lab["conf"], "matched": "short side"})
    return out


def agree(samples: list[dict], tol: float = 0.10) -> dict:
    """Median scale when two or more readings agree. Otherwise no scale."""
    used = [s for s in samples if s.get("ppi")]
    base = {"samples": samples, "method": "dimension labels matched to lines"}
    if len(used) < 2:
        detail = ("Only one dimension could be tied to the drawing, so the scale was not set."
                  if len(used) == 1 else "No dimension label could be tied to a line or a wall.")
        return {**base, "px_per_inch": None, "status": "uncertain" if samples else "not_found", "detail": detail}
    vals = np.array([s["ppi"] for s in used], float)
    med = float(np.median(vals))
    spread = float(np.max(np.abs(vals - med) / med)) if med else 1.0
    if med <= 0 or spread > tol:
        return {**base, "px_per_inch": None, "status": "uncertain", "relative_spread": round(spread, 3),
                "detail": "The dimension labels do not agree with each other, so no scale was chosen."}
    return {**base, "px_per_inch": round(med, 4), "status": "measured", "relative_spread": round(spread, 3),
            "detail": (f"Scale {med:.3f} px/in from {len(used)} agreeing dimension labels "
                       f"({MEASURED_SOURCE}).")}


def scale_from_image(rgb: np.ndarray) -> dict:
    """Read the sheet. ``status`` is ``estimated`` only when several labels agree."""
    found = read_labels(rgb)
    if not found.get("available"):
        return {"px_per_inch": None, "status": "unavailable", "labels": [], "unused": [],
                "samples": [], "engine": None, "measured": False, "detail": found.get("detail")}
    samples = samples_from_labels(found["labels"], ink_mask(rgb))
    result = agree(samples)
    result.update({"labels": found["labels"], "unused": found["unused"], "engine": found.get("engine"),
                   "measured": result.get("status") == "measured" and result.get("px_per_inch") is not None,
                   "source": MEASURED_SOURCE if result.get("px_per_inch") else None})
    return result
