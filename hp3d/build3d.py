"""Stage 3: extrude segmented 2D polygons into a 3D shell (trimesh + shapely).

Open-top style: floor slab + full-height walls; door openings are left as gaps
(door polygons are not extruded); windows become a lower sill and an upper
lintel so the opening is visible.
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union


def _poly(p: dict, s: float) -> Polygon | None:
    if len(p["outer"]) < 3:
        return None
    g = Polygon(np.asarray(p["outer"]) * s, [np.asarray(h) * s for h in p.get("holes", []) if len(h) >= 3])
    g = g.buffer(0)
    return g if g.area > 0 else None


def _extrude(geom, height: float, z0: float = 0.0) -> list[trimesh.Trimesh]:
    out = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty or g.area <= 0:
            continue
        m = trimesh.creation.extrude_polygon(g, height)
        m.apply_translation([0, 0, z0])
        out.append(m)
    return out


def build(polys: dict, inches_per_px: float, wall_h: float = 96.0, floor_t: float = 4.0,
          sill: float = 36.0, lintel: float = 80.0) -> dict[str, trimesh.Trimesh]:
    s = inches_per_px
    walls = unary_union([g for p in polys.get("wall", []) if (g := _poly(p, s))])
    wins = unary_union([g for p in polys.get("window", []) if (g := _poly(p, s))])
    all_geom = unary_union([walls, wins])  # doors excluded: stray 'door' blobs (e.g. captions) would inflate the slab
    parts: dict[str, list] = {"walls": _extrude(walls, wall_h, floor_t),
                              "windows": _extrude(wins, sill, floor_t) + _extrude(wins, wall_h - lintel, floor_t + lintel)}
    # Floor slab: convex-ish footprint = bounding hull of all structure.
    parts["floor"] = _extrude(all_geom.convex_hull, floor_t, 0.0) if not all_geom.is_empty else []
    meshes = {k: trimesh.util.concatenate(v) for k, v in parts.items() if v}
    # Flip Y (image rows grow downward) so the model is not mirrored.
    for m in meshes.values():
        m.apply_transform(np.diag([1, -1, 1, 1]))
        m.invert()  # mirroring flips winding; restore outward normals
    return meshes
