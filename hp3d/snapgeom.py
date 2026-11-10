"""Snap plan outlines to straight lines and right angles when the drawing already fits.

A wall that is a few degrees off horizontal or vertical is straightened. A corner
that is already near 90 degrees is squared. A line that would have to move a
long way, or a corner that is nowhere near a right angle, is left as drawn and
reported. Nothing is forced onto a rectangle the plan does not show.
"""
from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon

# Kinds whose outlines are structural. Furniture is left alone.
SNAP_KINDS = ("wall", "window", "door", "stair", "floor")

ANGLE_TOL_DEG = 7.0
MAX_SHIFT_FRAC = 0.10
MAX_SHIFT_PX = 3.0
AREA_TOL = 0.08


def _closed(pts: np.ndarray) -> bool:
    return len(pts) >= 2 and float(np.max(np.abs(pts[0] - pts[-1]))) <= 0.6


def _edge_length(pts: np.ndarray, i: int, j: int) -> float:
    return float(np.hypot(*(pts[j] - pts[i])))


def snap_ring(points, angle_tol: float = ANGLE_TOL_DEG, max_shift_frac: float = MAX_SHIFT_FRAC,
              max_shift_px: float = MAX_SHIFT_PX) -> tuple[list, list[dict]]:
    """Return (new ring, notes). Notes record each edge that was snapped or deliberately left."""
    pts = np.asarray(points, float)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] < 2:
        return [list(map(float, p[:2])) for p in np.atleast_2d(pts)], []
    wrapped = _closed(pts)
    if wrapped:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return pts.tolist(), []
    notes: list[dict] = []
    orient: list[str | None] = [None] * n
    for i in range(n):
        j = (i + 1) % n
        d = pts[j] - pts[i]
        length = float(np.hypot(*d))
        if length < 2.0:
            continue
        ang = abs(float(np.degrees(np.arctan2(d[1], d[0])))) % 180.0
        off_h = min(ang, 180.0 - ang)
        off_v = abs(ang - 90.0)
        off = min(off_h, off_v)
        if off > angle_tol:
            if length >= 15.0:
                notes.append({"edge": i, "action": "left", "length_px": round(length, 1),
                              "reason": f"off an axis by {off:.1f} deg, so it was not straightened"})
            continue
        want = "h" if off_h <= off_v else "v"
        shift = abs(float(d[1] if want == "h" else d[0])) / 2.0
        limit = max(max_shift_px, max_shift_frac * length)
        if shift > limit:
            notes.append({"edge": i, "action": "left", "length_px": round(length, 1),
                          "reason": f"straightening would move an endpoint {shift:.1f}px"})
            continue
        orient[i] = want

    parent_y = list(range(n))
    parent_x = list(range(n))

    def find(parent: list[int], i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(parent: list[int], i: int, j: int) -> None:
        a, b = find(parent, i), find(parent, j)
        if a != b:
            parent[b] = a

    for i, kind in enumerate(orient):
        j = (i + 1) % n
        if kind == "h":
            union(parent_y, i, j)
        elif kind == "v":
            union(parent_x, i, j)

    new = pts.copy()

    def apply_axis(parent: list[int], coord: int) -> None:
        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(parent, i), []).append(i)
        for idxs in groups.values():
            if len(idxs) < 2:
                continue
            lengths = []
            for i in idxs:
                j = (i + 1) % n
                if orient[i] is not None:
                    lengths.append(_edge_length(pts, i, j))
            shortest = min(lengths) if lengths else max_shift_px
            limit = max(max_shift_px, max_shift_frac * max(shortest, 1.0))
            val = float(np.mean(pts[idxs, coord]))
            move = max(abs(val - float(pts[i, coord])) for i in idxs)
            if move > limit:
                notes.append({"edge": int(idxs[0]), "action": "left",
                              "reason": f"a right-angle snap would move a corner {move:.1f}px"})
                continue
            new[idxs, coord] = val
            for i in idxs:
                if orient[i] is not None:
                    notes.append({"edge": i, "action": "snapped", "shift_px": round(move, 2)})

    apply_axis(parent_y, 1)
    apply_axis(parent_x, 0)
    _square_free_corners(pts, new, orient, notes, angle_tol, max_shift_frac, max_shift_px)

    try:
        before = Polygon(pts)
        after = Polygon(new)
    except Exception:  # noqa: BLE001
        before = after = None
    if (before is None or after is None or not after.is_valid or after.area <= 0 or before.area <= 0
            or abs(after.area - before.area) / before.area > AREA_TOL):
        notes.append({"edge": None, "action": "reverted",
                      "reason": "snapping would have changed the outline, so the drawn shape was kept"})
        out = pts
    else:
        out = new
    seq = out.tolist()
    cleaned = [seq[0]]
    for p in seq[1:]:
        if abs(p[0] - cleaned[-1][0]) > 0.4 or abs(p[1] - cleaned[-1][1]) > 0.4:
            cleaned.append(p)
    if len(cleaned) >= 2 and abs(cleaned[0][0] - cleaned[-1][0]) <= 0.4 and abs(cleaned[0][1] - cleaned[-1][1]) <= 0.4:
        cleaned.pop()
    if wrapped:
        cleaned.append(cleaned[0])
    return [[round(float(x), 2), round(float(y), 2)] for x, y in cleaned], notes


def _square_free_corners(orig, new, orient, notes, angle_tol, max_shift_frac, max_shift_px) -> None:
    """Square a corner whose edges are not axis-aligned but already meet near 90 degrees."""
    n = len(orig)
    for i in range(n):
        prev = (i - 1) % n
        nxt = (i + 1) % n
        if orient[prev] is not None or orient[i] is not None:
            continue
        v0, v1, v2 = new[prev], new[i], new[nxt]
        d1, d2 = v1 - v0, v2 - v1
        l1, l2 = float(np.hypot(*d1)), float(np.hypot(*d2))
        if l1 < 8.0 or l2 < 8.0:
            continue
        cos = float(np.clip(np.dot(d1, d2) / (l1 * l2), -1.0, 1.0))
        ang = float(np.degrees(np.arccos(cos)))
        err = abs(ang - 90.0)
        if err <= 0.8:
            continue
        if err > angle_tol:
            notes.append({"edge": i, "action": "left", "length_px": round(min(l1, l2), 1),
                          "reason": f"corner is {ang:.0f} deg, not a right angle"})
            continue
        if l1 <= l2:
            anchor, other, length, idx = v1, d2, l1, prev
            direction = _unit_perp(other, v1 - v0)
            target = anchor - direction * length
            shift = float(np.hypot(*(target - v0)))
        else:
            anchor, other, length, idx = v1, d1, l2, i
            direction = _unit_perp(other, v2 - v1)
            target = anchor + direction * length
            shift = float(np.hypot(*(target - v2)))
        limit = max(max_shift_px, max_shift_frac * length)
        if shift > limit:
            notes.append({"edge": i, "action": "left",
                          "reason": f"squaring the corner would move a point {shift:.1f}px"})
            continue
        if idx == prev:
            new[prev] = target
        else:
            new[nxt] = target
        notes.append({"edge": i, "action": "snapped", "shift_px": round(shift, 2),
                      "reason": "corner squared to a right angle"})


def _unit_perp(axis: np.ndarray, ref: np.ndarray) -> np.ndarray:
    p = np.array([-axis[1], axis[0]], float)
    n = float(np.hypot(*p))
    if n < 1e-9:
        return np.array([1.0, 0.0])
    p /= n
    if float(np.dot(p, ref)) < 0:
        p = -p
    return p


def snap_polygons(polys: dict, kinds: tuple[str, ...] = SNAP_KINDS) -> tuple[dict, dict]:
    """Snap structural rings. Returns (polygons, report). Rings that do not fit are unchanged."""
    out = {k: list(v) for k, v in polys.items()}
    snapped = left = reverted = 0
    samples: list[dict] = []
    for kind in kinds:
        nxt = []
        for i, poly in enumerate(out.get(kind, [])):
            ring, notes = snap_ring(poly.get("outer", []))
            holes = []
            for h in poly.get("holes", []):
                hr, hn = snap_ring(h)
                holes.append(hr)
                notes.extend(hn)
            actions = {n.get("action") for n in notes}
            if "reverted" in actions:
                reverted += 1
                nxt.append(poly)
            else:
                item = dict(poly)
                item["outer"] = ring
                item["holes"] = holes
                nxt.append(item)
            snapped += sum(1 for n in notes if n.get("action") == "snapped")
            left += sum(1 for n in notes if n.get("action") == "left")
            for n in notes:
                if n.get("action") in {"left", "reverted"} and len(samples) < 12:
                    samples.append({"kind": kind, "index": i, **{k: v for k, v in n.items() if k != "edge" or v is not None}})
        if kind in out or nxt:
            out[kind] = nxt
    return out, {"snapped_edges": snapped, "left_edges": left, "reverted_rings": reverted, "samples": samples}
