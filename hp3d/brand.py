"""Maker's mark: recess the 8K by AK logo into the underside of the floor slab.

Runs on every generation (``pipeline.run`` and ``editor.rebuild``) right after
mesh repair, on the ``floor`` part only. Nothing else in the model changes.

Units: the house is modelled in inches and exported unitless (STL) or tagged
millimetres (trimesh 3MF), so a slicer reads 1 model unit as 1 mm unless the
print is rescaled. ``mm_per_unit`` (default 1.0) is that print scale; the logo
is sized in printed millimetres and converted to model units with it. If the
model is scaled in the slicer, the mark scales with it.

Choice and placement:
- Full lockup (8K / coral bar / "by AK") at native size (75 mm mark from the
  80 mm file) when it fits on
  the base with ``MARGIN_MM`` clearance, shrunk down to ``FULL_MIN_MM`` wide if
  needed; below that "by AK" will not print, so the 8K-only mark is used
  (native 50 mm, down to ``MINI_MIN_MM``). Never scaled above native size.
- Tried at 0 deg first, 90 deg only if that gives a bigger mark.
- Mirrored in X so it reads correctly when the print is flipped over and the
  bottom is viewed.
- The shipped relief STLs are negatives (plate with letter holes); ``_positive``
  turns them back into the letter shapes before cutting.
- Recessed (debossed) ``min(DEPTH_MM, 40% of slab)`` deep, so the bottom stays
  flat on the bed, the slab is never cut through, and the part stays watertight.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import polylabel, unary_union

from . import ROOT

LOGO_DIR = ROOT / "assets" / "logo"
VARIANTS = {  # name: (file, plate width mm, smallest printable mark width mm)
    "8K-by-AK": ("8K-by-AK-relief-80mm.stl", 80.0, 50.0),
    "8K-only": ("8K-only-relief-50mm.stl", 50.0, 15.0),
}
FULL_MIN_MM = VARIANTS["8K-by-AK"][2]
MINI_MIN_MM = VARIANTS["8K-only"][2]
MARGIN_MM = 4.0
DEPTH_MM = 1.0          # ~5 layers at 0.2 mm; shallow enough to bridge cleanly
MIN_DEPTH_MM = 0.3
OVERCUT_MM = 0.3        # cutter pokes below the bottom face to avoid coplanar booleans


@lru_cache(maxsize=None)
def _logo(name: str) -> tuple[trimesh.Trimesh, Polygon]:
    m = trimesh.load(LOGO_DIR / VARIANTS[name][0], force="mesh")
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    m = _positive(m)
    return m, MultiPoint(m.vertices[:, :2]).convex_hull


def _covered(m: trimesh.Trimesh, pt) -> bool:
    """Is 2D point pt under an upward-facing face (i.e. is the solid there)? No rtree needed."""
    t = m.triangles[m.face_normals[:, 2] > 0.5][:, :, :2]
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    v0, v1, v2 = c - a, b - a, np.asarray(pt) - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d02, d12 = (v0 * v2).sum(1), (v1 * v2).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-12
    u = np.where(ok, (d11 * d02 - d01 * d12) / np.where(ok, den, 1), -1)
    v = np.where(ok, (d00 * d12 - d01 * d02) / np.where(ok, den, 1), -1)
    return bool(np.any((u >= 0) & (v >= 0) & (u + v <= 1)))


def _positive(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """The shipped *-relief STLs are negatives (solid plate, letters as holes).

    If the solid fills the bounding-box corners, it is a plate: subtract it from
    its own bounding box so only the letters, divider and the 8's real shape
    remain. A true positive file passes through unchanged.
    """
    lo, hi = m.bounds
    inset = 0.02 * (hi - lo)
    corners = np.array([[x, y] for x in (lo[0] + inset[0], hi[0] - inset[0]) for y in (lo[1] + inset[1], hi[1] - inset[1])])
    if sum(_covered(m, c) for c in corners) < 3:
        return m
    box = trimesh.creation.box(bounds=[lo, hi])
    pos = trimesh.boolean.difference([box, m], engine="manifold")
    pos.remove_unreferenced_vertices()
    return pos


def underside(m: trimesh.Trimesh, tol: float = 1e-3) -> tuple[Polygon, float]:
    """2D outline of the downward-facing faces at the mesh's lowest Z."""
    z0 = m.bounds[0][2]
    sel = (m.face_normals[:, 2] < -0.99) & (np.abs(m.triangles[:, :, 2] - z0).max(axis=1) < tol)
    tris = [Polygon(t[:, :2]) for t in m.triangles[sel]]
    g = unary_union([t.buffer(1e-6) for t in tris if t.area > 0])
    return g, z0


def _fit(base, hull: Polygon, native_w: float, unit: float, margin: float, rot: float) -> tuple[float, tuple]:
    """Largest scale k<=1 so the (mirrored, rotated) logo hull + margin fits inside base."""
    if base.is_empty or base.area <= 0:
        return 0.0, (0.0, 0.0)
    biggest = max(getattr(base, "geoms", [base]), key=lambda g: g.area)
    shape = affinity.rotate(affinity.scale(hull, -1, 1, origin=(0, 0)), rot, origin=(0, 0))
    best = (0.0, (0.0, 0.0))
    # Centroid first (looks centred on a normal slab), pole of inaccessibility for odd shapes.
    for c in (biggest.centroid, polylabel(biggest, tolerance=max(1e-3, biggest.length * 1e-4))):
        def fits(k, c=c):
            g = affinity.translate(affinity.scale(shape, k / unit, k / unit, origin=(0, 0)), c.x, c.y)
            return biggest.contains(g.buffer(margin / unit))

        if fits(1.0):
            k = 1.0
        else:
            lo, hi = 0.0, 1.0
            for _ in range(30):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if fits(mid) else (lo, mid)
            k = lo
        if k > best[0] + 1e-6:
            best = (k, (c.x, c.y))
        if k >= 1.0:
            break
    return best


def choose(base, mm_per_unit: float = 1.0) -> dict | None:
    """Pick variant/scale/rotation for a base outline (model units). None if nothing printable fits."""
    unit = mm_per_unit  # mm per model unit
    for name, (_, _plate_w, min_w) in VARIANTS.items():
        _, hull = _logo(name)
        native = hull.bounds[2] - hull.bounds[0]     # printed width of the mark itself (not the file's plate)
        best = None
        for rot in (0.0, 90.0):
            k, c = _fit(base, hull, native, unit, MARGIN_MM, rot)
            if best is None or k > best["k"] + 1e-6:
                best = {"variant": name, "k": k, "center": c, "rotation_deg": rot}
            if k >= 1.0:
                break
        if best and best["k"] * native >= min_w:
            best["width_mm"] = round(best["k"] * native, 2)
            return best
    return None


def apply_underside_logo(parts: dict[str, trimesh.Trimesh], mm_per_unit: float = 1.0) -> dict:
    """Recess the mark into parts['floor'] in place. Returns a report dict (never raises on 'no room')."""
    rep: dict = {"mm_per_model_unit": mm_per_unit}
    floor = parts.get("floor")
    if floor is None or len(floor.faces) == 0:
        return {**rep, "status": "skipped", "reason": "no floor slab to mark"}
    if floor.volume < 0:
        floor.invert()
    base, z0 = underside(floor)
    t_mm = float(floor.bounds[1][2] - z0) * mm_per_unit
    depth = min(DEPTH_MM, 0.4 * t_mm)
    if depth < MIN_DEPTH_MM:
        return {**rep, "status": "skipped", "reason": f"slab {t_mm:.2f} mm too thin for a recess"}
    pick = choose(base, mm_per_unit)
    if pick is None:
        bx = np.ptp(np.asarray(base.bounds).reshape(2, 2), axis=0) * mm_per_unit if not base.is_empty else [0, 0]
        return {**rep, "status": "skipped",
                "reason": f"base ~{bx[0]:.0f}x{bx[1]:.0f} mm too small for an {MINI_MIN_MM:.0f} mm 8K mark + margin"}
    logo, _ = _logo(pick["variant"])
    s = pick["k"] / mm_per_unit
    zs = (depth + OVERCUT_MM) / mm_per_unit / logo.bounds[1][2]
    cutter = logo.copy()
    T = np.diag([-s, s, zs, 1.0])                    # mirror X: reads right when the print is flipped over
    R = trimesh.transformations.rotation_matrix(np.radians(pick["rotation_deg"]), [0, 0, 1])
    cutter.apply_transform(R @ T)
    cutter.apply_translation([pick["center"][0], pick["center"][1], z0 - OVERCUT_MM / mm_per_unit])
    cut = trimesh.boolean.difference([floor, cutter], engine="manifold")
    if not cut.is_watertight or cut.volume >= floor.volume:
        return {**rep, "status": "error", "reason": "boolean recess failed; floor left unmarked"}
    removed = floor.volume - cut.volume
    parts["floor"] = cut
    return {**rep, "status": "ok", "variant": pick["variant"], "width_mm": pick["width_mm"],
            "depth_mm": round(depth, 2), "rotation_deg": pick["rotation_deg"],
            "center_model": [round(v, 2) for v in pick["center"]], "mirrored": True, "placement": "recessed, underside",
            "floor_watertight": bool(cut.is_watertight), "removed_volume_model": round(float(removed), 3)}
