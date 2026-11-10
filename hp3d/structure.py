"""Merge classical symbol detection into the polygon set the 3D build already uses.

When a segmentation stage has already produced walls, those walls are kept and
only straightened. Windows, stairs and door openings are added from the drawing.
When there are no segmented walls, the classical wall pairs are used instead.
Low-confidence marks stay in the report and do not become polygons.
"""
from __future__ import annotations

import numpy as np

from .snapgeom import snap_polygons
from .symbols import detect


def _box(poly: dict) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly.get("outer", [])]
    ys = [p[1] for p in poly.get("outer", [])]
    if not xs:
        return 0, 0, 0, 0
    return min(xs), min(ys), max(xs), max(ys)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(a[2] - a[0], 0) * max(a[3] - a[1], 0)
    bb = max(b[2] - b[0], 0) * max(b[3] - b[1], 0)
    return inter / max(aa + bb - inter, 1e-6)


def apply(rgb: np.ndarray, polys: dict | None = None) -> dict:
    """Return polygons plus the detection and snap reports. Does not invent missing parts."""
    found = detect(np.asarray(rgb))
    base = {k: list(v) for k, v in (polys or {}).items()}
    had_walls = bool(base.get("wall"))
    if not had_walls:
        base["wall"] = found["walls"]
        wall_source = "classical-lines"
    else:
        wall_source = "segmentation"
    have = [_box(p) for p in base.get("window", [])]
    added_windows = []
    for win in found["windows"]:
        box = _box(win)
        if any(_iou(box, old) > 0.45 for old in have):
            continue
        added_windows.append(win)
        have.append(box)
    base.setdefault("window", []).extend(added_windows)
    # Door swings are openings only. A leaf drawn inside the swing is not copied in.
    have_doors = [_box(p) for p in base.get("door", [])]
    added_doors = []
    for door in found["doors"]:
        box = _box(door)
        if any(_iou(box, old) > 0.45 for old in have_doors):
            continue
        added_doors.append(door)
        have_doors.append(box)
    base.setdefault("door", []).extend(added_doors)
    base["stair"] = found["stairs"]
    snapped, snap_report = snap_polygons(base)
    summary = {
        "wall_source": wall_source,
        "walls": len(snapped.get("wall", [])),
        "windows": len(snapped.get("window", [])),
        "windows_from_symbols": len(added_windows),
        "doors": len(snapped.get("door", [])),
        "doors_from_swings": len(added_doors),
        "stairs": len(snapped.get("stair", [])),
        "stair_treads": [s.get("n_treads") for s in snapped.get("stair", [])],
        "uncertain": len(found.get("uncertain") or []),
        "snap": {"snapped_edges": snap_report["snapped_edges"], "left_edges": snap_report["left_edges"],
                 "reverted_rings": snap_report["reverted_rings"]},
    }
    return {"polys": snapped, "detected": found, "snap": snap_report, "summary": summary}
