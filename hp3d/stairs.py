"""Stepped solid for a stair run whose treads were counted on the plan.

The footprint and the tread count come from the drawing. The riser height is
not decided here: the caller passes the total rise it was given. When the plan
does not print a rise, that total is a placeholder and the confidence report
says so.
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import Polygon, box


def _ring(poly: dict, scale: float) -> Polygon | None:
    outer = poly.get("outer") or []
    if len(outer) < 3:
        return None
    g = Polygon(np.asarray(outer, float) * scale)
    g = g.buffer(0)
    return g if g.area > 0 else None


def _slices(geom: Polygon, n: int, travel: str) -> list[Polygon]:
    """N overlapping slices from the low end through each tread, low end first."""
    minx, miny, maxx, maxy = geom.bounds
    along_y = travel != "x"
    c0, c1 = (miny, maxy) if along_y else (minx, maxx)
    if c1 - c0 <= 0 or n < 2:
        return []
    edges = np.linspace(c0, c1, n + 1)
    out = []
    for i in range(n):
        hi = float(edges[i + 1])
        # A hair of overlap so the stacked solids union into one piece.
        pad = 0.04 if i < n - 1 else 0.0
        if along_y:
            sl = box(minx - 1.0, c0 - 0.02, maxx + 1.0, hi + pad)
        else:
            sl = box(c0 - 0.02, miny - 1.0, hi + pad, maxy + 1.0)
        piece = geom.intersection(sl)
        if not piece.is_empty and piece.area > 0:
            out.append(piece)
    return out


def _extrude(geom, height: float, z0: float) -> list[trimesh.Trimesh]:
    meshes = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty or g.area <= 0:
            continue
        m = trimesh.creation.extrude_polygon(g, height)
        m.apply_translation([0, 0, z0])
        meshes.append(m)
    return meshes


def stair_meshes(stair_polys: list[dict], inches_per_px: float, floor_t: float, wall_h: float) -> list[trimesh.Trimesh]:
    """One watertight solid per stair run. An unreadable run is skipped, not replaced with a ramp."""
    solids: list[trimesh.Trimesh] = []
    for poly in stair_polys or []:
        geom = _ring(poly, inches_per_px)
        n = int(poly.get("n_treads") or 0)
        if geom is None or n < 2 or wall_h <= 0:
            continue
        per = poly.get("rise_in")
        if per is not None and float(per) > 0:
            total = float(per) * n
        else:
            total = float(wall_h)
        if total <= 0:
            continue
        slices = _slices(geom, n, str(poly.get("travel") or "y"))
        if len(slices) < 2:
            continue
        parts: list[trimesh.Trimesh] = []
        for i, sl in enumerate(slices):
            height = total * (i + 1) / n
            parts.extend(_extrude(sl, height, floor_t))
        if not parts:
            continue
        try:
            fused = trimesh.boolean.union(parts, engine="manifold")
            if fused is not None and len(fused.faces) and fused.volume > 0:
                solids.append(fused)
                continue
        except Exception:  # noqa: BLE001
            pass
        solids.extend(parts)
    return solids
