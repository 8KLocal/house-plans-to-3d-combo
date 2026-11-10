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


def polygon_area(poly: dict) -> float:
    pts = np.asarray(poly.get("outer") or [], float)
    if pts.ndim != 2 or len(pts) < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) * 0.5


def wall_area(polys) -> float:
    return sum(polygon_area(p) for p in (polys or []))


def choose_walls(segmented, classical, shape: tuple[int, int]) -> tuple[list, str, str | None]:
    """Keep the segmented walls unless they are empty or a sliver of the drawing.

    A handful of scraps must not hide a real line tracing. The message is for the user.
    """
    segmented = list(segmented or [])
    classical = list(classical or [])
    plan = float(max(1, int(shape[0]) * int(shape[1])))
    got, traced = wall_area(segmented), wall_area(classical)
    tiny = (not segmented) or got < 0.004 * plan or (len(segmented) <= 3 and got < 0.02 * plan)
    traced_is_real = len(classical) >= 4 and traced >= max(got * 3.0, 0.008 * plan)
    if tiny and traced_is_real:
        return classical, "classical-lines", (
            "The first wall reading was almost empty, so I traced the wall lines on the drawing instead. "
            "Please check that the walls look right.")
    if segmented:
        return segmented, "segmentation", None
    if classical:
        return classical, "classical-lines", (
            "I couldn't read a wall mask, so I traced the wall lines on the drawing instead.")
    return [], "none", "I couldn't find walls on this plan."


def apply(rgb: np.ndarray, polys: dict | None = None) -> dict:
    """Return polygons plus the detection and snap reports. Does not invent missing parts."""
    found = detect(np.asarray(rgb))
    base = {k: list(v) for k, v in (polys or {}).items()}
    walls, wall_source, wall_note = choose_walls(base.get("wall"), found["walls"], np.asarray(rgb).shape[:2])
    base["wall"] = walls
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
        "wall_note": wall_note,
        "wall_area_px": round(wall_area(snapped.get("wall", [])), 1),
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
