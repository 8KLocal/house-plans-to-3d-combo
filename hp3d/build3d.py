"""Stage 3: extrude segmented 2D polygons into a 3D shell (trimesh + shapely).

Open-top style: floor slab + full-height walls; door openings are left as gaps
(door polygons are not extruded); windows become a lower sill and an upper
lintel so the opening is visible. Door polygons marked ``cut`` (added in the
app or read from a 3D file) are also cut out of the walls, so a door frame
opening always exists where a door is listed. Furniture polygons (only those
on the plan or in the 3D file) become simple blocks standing on the floor:
``height_px`` (measured, pixel units) wins, otherwise ``furniture_h``.

``frame_trim`` > 0 (the app and pipeline use ``FRAME_TRIM`` inches) splits the
wall material within that distance of each door opening, plus any header above
it, into a separate ``door_frames`` part so it can be coloured like the plan
(orange). Geometry is unchanged: walls + door_frames cover exactly what walls
alone did. With the default 0 everything stays in ``walls``.
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .stairs import stair_meshes

FRAME_TRIM = 4.0   # inches of wall either side of / around a doorway drawn as the door frame


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
          sill: float = 36.0, lintel: float = 80.0, furniture_h: float = 30.0,
          frame_trim: float = 0.0) -> dict[str, trimesh.Trimesh]:
    s = inches_per_px
    walls = unary_union([g for p in polys.get("wall", []) if (g := _poly(p, s))])
    cuts = unary_union([g for p in polys.get("door", []) if (p.get("cut") or p.get("added_in_app")) and (g := _poly(p, s))])
    if not cuts.is_empty:
        walls = walls.difference(cuts)
    wins = unary_union([g for p in polys.get("window", []) if (g := _poly(p, s))])
    # Floor still covers the openings. The wall itself loses the window footprint so the
    # sill and head (extruded below) are a cut-out, not a solid wall with a picture on it.
    pieces = [g for g in (walls, wins) if g is not None and not g.is_empty]
    all_geom = unary_union(pieces) if pieces else walls
    if not wins.is_empty and not walls.is_empty:
        walls = walls.difference(wins).buffer(0)
    if not (wall_h > 0 and floor_t > 0 and 0 <= sill < lintel <= wall_h):
        raise ValueError(f"need wall_h>0, floor_t>0 and 0 <= sill < lintel <= wall_h (got wall_h={wall_h}, "
                         f"floor_t={floor_t}, sill={sill}, lintel={lintel})")
    win_parts = (_extrude(wins, sill, floor_t) if sill > 0 else []) + \
        (_extrude(wins, wall_h - lintel, floor_t + lintel) if wall_h > lintel else [])
    frames = []
    doors = [g for p in polys.get("door", []) if (g := _poly(p, s))]
    if frame_trim > 0 and doors and not walls.is_empty:
        zone = unary_union([g.buffer(frame_trim, join_style=2) for g in doors])
        jambs = walls.intersection(zone).buffer(0)
        if not jambs.is_empty and jambs.area > 0:
            walls = walls.difference(zone).buffer(0)
            frames += _extrude(jambs, wall_h, floor_t)
    parts: dict[str, list] = {"walls": _extrude(walls, wall_h, floor_t), "windows": win_parts}
    # Door frames read from a 3D file keep the header (wall above the opening) at its measured height.
    for p in polys.get("door", []):
        if p.get("head_px") and (g := _poly(p, s)) is not None and p["head_px"] * s < wall_h:
            head = _extrude(g, wall_h - p["head_px"] * s, floor_t + p["head_px"] * s)
            (frames if frame_trim > 0 else parts["walls"]).extend(head)
    if frames:
        parts["door_frames"] = frames
    furn = []
    for p in polys.get("furniture", []):
        g = _poly(p, s)
        if g is None:
            continue
        h = p["height_px"] * s if p.get("height_px") else furniture_h
        h = min(float(h), wall_h)
        if h > 0:
            furn += _extrude(g, h, floor_t)
    if furn:
        parts["furniture"] = furn
        all_geom = unary_union([all_geom] + [g for p in polys.get("furniture", []) if (g := _poly(p, s))])
    stair_parts = stair_meshes(polys.get("stair") or [], s, floor_t, wall_h)
    if stair_parts:
        parts["stairs"] = stair_parts
        stair_foot = unary_union([g for p in polys.get("stair", []) if (g := _poly(p, s))])
        if not stair_foot.is_empty:
            all_geom = unary_union([all_geom, stair_foot])
    # Floor slab: convex-ish footprint = bounding hull of all structure.
    # A footprint read from a 3D file ("floor" polygons) is used as-is; otherwise the hull of the structure.
    fl = unary_union([g for p in polys.get("floor", []) if (g := _poly(p, s))])
    slab = unary_union([fl, all_geom]).buffer(0) if not fl.is_empty else all_geom.convex_hull
    parts["floor"] = _extrude(slab, floor_t, 0.0) if not all_geom.is_empty else []
    meshes = {k: trimesh.util.concatenate(v) for k, v in parts.items() if v}
    # Flip Y (image rows grow downward) so the model is not mirrored.
    for m in meshes.values():
        m.apply_transform(np.diag([1, -1, 1, 1]))
        # trimesh.apply_transform already re-winds faces for a mirroring matrix,
        # so normals stay outward (an extra invert() here turned parts inside-out).
    return meshes


def merged_for_stl(parts: dict[str, trimesh.Trimesh]) -> trimesh.Trimesh:
    """One mesh for model.stl. Door frames share whole faces with the walls they were split from,
    so they are fused back into the walls first (STL is single-colour anyway); otherwise welding
    the STL would leave doubled internal faces and the walls would stop being watertight."""
    walls, frames = parts.get("walls"), parts.get("door_frames")
    rest = [m for k, m in parts.items() if k not in ("walls", "door_frames")]
    if walls is not None and frames is not None:
        try:
            fused = trimesh.boolean.union([walls, frames], engine="manifold")
            ok = fused.is_watertight and abs(fused.volume - (walls.volume + frames.volume)) <= 1e-3 * max(fused.volume, 1e-9)
        except Exception:  # noqa: BLE001
            ok = False
        body = [fused] if ok else [walls, frames]
    else:
        body = [m for m in (walls, frames) if m is not None]
    return trimesh.util.concatenate(body + rest)
