"""Stage 2: printed dimensions from vector PDFs with jasoncobra3/Floorplan-Dimractor.

Upstream has NO license file (see INTEGRATION.md): we call it in-process but do
not modify or redistribute it.

Dimractor reports every dimension found in a text span with that span's bbox
(PDF points). A room label like ``12'0" X 12'0"`` therefore yields two
dimensions with an identical bbox; we group those into (w, h) pairs and use
them to estimate drawing scale against the segmented room the label sits in.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

from ._paths import on_path


def extract(pdf_path: Path) -> dict:
    with on_path("Floorplan-Dimractor", purge_prefixes=("src",)):
        from src.pdf_processor import PDFProcessor  # Dimractor's own package name

        return PDFProcessor().extract_with_pymupdf(str(pdf_path))


def room_pairs(page: dict) -> list[dict]:
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for d in page.get("dimensions", []):
        groups.setdefault(tuple(round(v, 2) for v in d["bbox"]), []).append(d)
    pairs = []
    for bbox, ds in groups.items():
        if len(ds) == 2 and all(d["inches"] >= 24 for d in ds):  # ignore tiny/fixture dims
            pairs.append({"bbox_pt": list(bbox), "raw": [d["raw"] for d in ds],
                          "inches": [d["inches"] for d in ds]})
    return pairs


def estimate_scale(pairs: list[dict], wall_mask: np.ndarray, pt_to_px: float,
                   close_px: int = 25, agree_tol: float = 0.15) -> dict:
    """Estimate pixels-per-inch by matching room labels to enclosed regions.

    Door gaps are closed with a morphological close so rooms become separate
    connected components; the label centre picks its room; the room's bbox
    long/short sides are compared to the printed long/short dimensions.
    Returns the median plus every sample so the report can show the spread.
    """
    k = np.ones((close_px, close_px), np.uint8)
    closed = cv2.morphologyEx((wall_mask > 0).astype(np.uint8), cv2.MORPH_CLOSE, k)
    free = (closed == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    H, W = wall_mask.shape
    samples = []
    for p in pairs:
        x0, y0, x1, y1 = p["bbox_pt"]
        cx, cy = int((x0 + x1) / 2 * pt_to_px), int((y0 + y1) / 2 * pt_to_px)
        if not (0 <= cx < W and 0 <= cy < H):
            continue
        lab = labels[cy, cx]
        if lab == 0:
            continue
        bw, bh, area = stats[lab, cv2.CC_STAT_WIDTH], stats[lab, cv2.CC_STAT_HEIGHT], stats[lab, cv2.CC_STAT_AREA]
        if area > 0.25 * H * W:  # leaked into the outdoors / whole plan
            continue
        if min(bw, bh) < 3:
            continue
        long_px, short_px = max(bw, bh), min(bw, bh)
        long_in, short_in = max(p["inches"]), min(p["inches"])
        a, b = long_px / long_in, short_px / short_in
        # Long and short sides must imply the same scale; otherwise the region
        # leaked through an opening or merged with a neighbour.
        ok = abs(a - b) / max(a, b) <= agree_tol
        samples.append({"label": " x ".join(p["raw"]), "region": int(lab), "px": [int(long_px), int(short_px)],
                        "ppi": [a, b], "used": bool(ok)})
    # A region claimed by two or more different labels is an open-plan merge.
    from collections import Counter
    claims = Counter(s["region"] for s in samples)
    for s in samples:
        if claims[s["region"]] > 1:
            s["used"] = False
            s["reject"] = "region shared by several labels"
        elif not s["used"]:
            s["reject"] = "long/short sides disagree"
    vals = [v for s in samples if s["used"] for v in s["ppi"]]
    if not vals:
        return {"px_per_inch": None, "samples": samples, "method": "none"}
    med = float(np.median(vals))
    spread = float(np.median([abs(v - med) for v in vals]) / med)
    return {"px_per_inch": med, "relative_mad": spread, "samples": samples,
            "method": "room-label matching (median)"}
