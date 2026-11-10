"""Read a 3D model file (STL, OBJ, PLY, OFF, 3MF, GLB/glTF) into the editable job format.

No AI: the mesh is cut into horizontal slices and the slices are compared.

  floor slab   a band at the bottom whose slice covers most of the footprint
  walls        present low (12%), mid (50%) and high (93%) in the wall band
  windows      present low and high but missing at mid height (sill + lintel)
  door frames  present high but missing low and mid (an opening with a header);
               openings that go all the way up are just gaps and are reported as such
  low parts    present below but not at the top of the wall band: furniture,
               counters, half walls; kept only because the file has them

Heights (slab, wall, sill, lintel, each low part) are measured from the slices.
STL has no units, so the size is a guess until the operator gives one real
length; every guess is written to report.json as an assumption.

The result is the same job a plan image produces (polygons in pixels of a
top-view picture + job.json), so plain-language edits, Undo and Rebuild work
unchanged. Rebuild re-extrudes those polygons, so detail the slices cannot
describe (roofs, stairs, sloped or curved parts) is not carried over; the
report says so.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box as sbox
from shapely.ops import unary_union

MESH_EXT = {".stl", ".obj", ".ply", ".off", ".3mf", ".glb", ".gltf"}
Y_UP_EXT = {".glb", ".gltf"}          # glTF is Y-up by spec; everything else is read as Z-up
PLAN_MAX_PX = 900
PAD_PX = 20
LOW, MID, HIGH = 0.12, 0.50, 0.93     # slice heights as fractions of the wall band
N_LEVELS = 120

# Plausible floor-to-ceiling heights per unit, for a first size guess only.
UNIT_GUESS = [("inches", 1.0, 84, 132), ("millimetres", 1 / 25.4, 2100, 3400), ("centimetres", 1 / 2.54, 210, 340),
              ("metres", 39.3701, 2.1, 3.4), ("feet", 12.0, 7.0, 11.0)]


class UnreadableFile(ValueError):
    """Raised with a plain-language reason when a file cannot be used."""


# ---------------------------------------------------------------- loading

def _face_rgba(g: trimesh.Trimesh) -> np.ndarray | None:
    """Per-face RGBA of a mesh as the file coloured it (face, vertex or material colour), else None."""
    try:
        v = g.visual
        if v is None or v.kind is None:
            return None
        if v.kind == "texture":
            v = v.to_color()
        fc = np.asarray(v.face_colors)
    except Exception:  # noqa: BLE001  (odd materials: just treat as uncoloured)
        return None
    return fc[:, :4].astype(float) if fc.ndim == 2 and len(fc) == len(g.faces) else None


def color_samples(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(face centroids, face areas, RGBA) in the file's own coordinates, only if the file is really
    coloured: at least two clearly different colours, each on >= 1% of the surface."""
    try:
        loaded = trimesh.load(Path(path))
    except Exception:  # noqa: BLE001
        return None
    items = []
    if isinstance(loaded, trimesh.Scene):
        for node in loaded.graph.nodes_geometry:
            T, gname = loaded.graph[node]
            g = loaded.geometry.get(gname)
            if isinstance(g, trimesh.Trimesh) and len(g.faces):
                items.append((g, T))
    elif isinstance(loaded, trimesh.Trimesh):
        items.append((loaded, np.eye(4)))
    pts, areas, cols = [], [], []
    for g, T in items:
        fc = _face_rgba(g)
        if fc is None:
            continue
        pts.append(trimesh.transform_points(g.triangles_center, T)); areas.append(g.area_faces); cols.append(fc)
    if not pts:
        return None
    pts, areas, cols = np.vstack(pts), np.concatenate(areas), np.vstack(cols)
    q = (cols[:, :3] // 24).astype(int)
    keys, inv = np.unique(q, axis=0, return_inverse=True)
    share = np.bincount(inv.ravel(), weights=areas, minlength=len(keys)) / max(areas.sum(), 1e-12)
    if (share >= 0.01).sum() < 2:
        return None
    return pts, areas, cols


def source_colors(samples, a: "Analysis") -> dict[str, list[int]]:
    """Main colour (area-weighted) of each part as the source file painted it, keyed by built part name."""
    if samples is None:
        return {}
    import shapely
    pts, areas, cols = samples
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    tol = 0.6 * a.wall_t
    zr = 1e-3 * max(float(a.bounds[1][2] - a.bounds[0][2]), 1e-9)
    left = np.ones(len(pts), bool)
    label = np.full(len(pts), "", dtype=object)
    if a.slab:
        sel = z <= a.floor_top + zr
        label[sel] = "floor"; left &= ~sel
    up = left & (z > a.floor_top - zr) & (z <= a.wall_top + zr)

    def tag(name, geoms, buf):
        nonlocal left
        g = unary_union([p.buffer(buf) for p in geoms]) if geoms else Polygon()
        if g.is_empty:
            return
        sel = up & left & shapely.contains_xy(g, x, y)
        label[sel] = name; left &= ~sel

    tag("furniture", [p for p, _ in a.low], 0.25 * a.wall_t)
    tag("windows", [p for p, *_ in a.windows], tol)
    tag("door_frames", [p for p, _ in a.doors], tol)
    tag("walls", [a.walls], tol)
    out = {}
    for name in ("floor", "walls", "windows", "door_frames", "furniture"):
        sel = label == name
        if not sel.any():
            continue
        q = (cols[sel, :3] // 24).astype(int)
        keys, inv = np.unique(q, axis=0, return_inverse=True)
        best = int(np.argmax(np.bincount(inv.ravel(), weights=areas[sel], minlength=len(keys))))
        pick = inv.ravel() == best
        w = areas[sel][pick]
        c = (cols[sel][pick] * w[:, None]).sum(0) / max(w.sum(), 1e-12)
        out[name] = [int(round(v)) for v in c]
    return out


def load_mesh(path: str | Path) -> tuple[trimesh.Trimesh, list[str]]:
    path = Path(path)
    notes: list[str] = []
    try:
        m = trimesh.load(path, force="mesh")
    except Exception as e:  # noqa: BLE001
        raise UnreadableFile(f"I couldn't read the 3D file {path.name}: {type(e).__name__}: {e}") from e
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
        raise UnreadableFile(f"{path.name} has no 3D surfaces in it (empty, points only or lines only).")
    m = m.copy()
    T = np.eye(4)                 # file coordinates -> the coordinates sliced below (for colour samples)
    if path.suffix.lower() in Y_UP_EXT:
        R = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        m.apply_transform(R); T = R @ T
        notes.append("glTF/GLB file: Y axis read as up (glTF standard).")
    else:
        notes.append(f"{path.suffix.upper()[1:]} file: Z axis read as up (usual for 3D printing files).")
    T = trimesh.transformations.translation_matrix(-m.bounds[0]) @ T
    m.apply_translation(-m.bounds[0])
    if not m.is_watertight:
        try:
            from . import repair
            fixed = repair.repair(m)
            if len(fixed.faces):
                notes.append("The file was not watertight; it was repaired before slicing.")
                m = fixed
                T = trimesh.transformations.translation_matrix(-m.bounds[0]) @ T
                m.apply_translation(-m.bounds[0])
        except Exception as e:  # noqa: BLE001
            notes.append(f"The file is not watertight and repair failed ({e}); slices may have gaps.")
    ext = m.bounds[1] - m.bounds[0]
    if ext.min() <= 0:
        raise UnreadableFile(f"{path.name} is flat (no height or width), so there is nothing to read as walls.")
    cs = color_samples(path)
    if cs is not None:
        m.metadata["hp3d_color_samples"] = (trimesh.transform_points(cs[0], T), cs[1], cs[2])
    return m, notes


# ---------------------------------------------------------------- slicing

def section(m: trimesh.Trimesh, z: float):
    """Solid area of the mesh at height z, as a shapely geometry (even-odd of the cut loops)."""
    p = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if p is None:
        return Polygon()
    g = Polygon()
    for loop in p.discrete:
        if len(loop) < 3:
            continue
        poly = Polygon(np.asarray(loop)[:, :2]).buffer(0)
        if poly.area > 0:
            g = g.symmetric_difference(poly)
    return g.buffer(0)


def _clean(g, eps: float):
    """Drop slivers thinner than ~2*eps (float noise between nearly identical slices)."""
    if g.is_empty:
        return g
    g = g.buffer(-eps, join_style=2).buffer(eps, join_style=2)
    return g if not g.is_empty else Polygon()


def _parts(g) -> list[Polygon]:
    if g.is_empty:
        return []
    return [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and not p.is_empty]


@dataclass
class Analysis:
    bounds: np.ndarray
    levels: np.ndarray
    areas: np.ndarray
    slab: bool
    floor_top: float
    wall_top: float
    wall_t: float
    above_walls: str | None            # "roof or another floor" when something sits on the walls
    walls: object
    windows: list = field(default_factory=list)   # (Polygon, sill_u, lintel_u)
    doors: list = field(default_factory=list)     # (Polygon, head_u)
    low: list = field(default_factory=list)       # (Polygon, height_u)
    footprint: object = None
    notes: list = field(default_factory=list)


def _refine(m: trimesh.Trimesh, lo: float, hi: float, pick: str) -> float:
    z = np.unique(np.round(m.vertices[:, 2], 6))
    z = z[(z >= lo - 1e-9) & (z <= hi + 1e-9)]
    if not len(z):
        return (lo + hi) / 2
    return float(z.max() if pick == "max" else z.min())


def _snap(m: trimesh.Trimesh, z: float, tol: float) -> float:
    """Move a slice-resolution height onto the nearest real vertex height within tol."""
    zs = np.unique(np.round(m.vertices[:, 2], 6))
    near = zs[np.abs(zs - z) <= tol]
    return float(near[np.argmin(np.abs(near - z))]) if len(near) else float(z)


def analyze(m: trimesh.Trimesh, n: int = N_LEVELS) -> Analysis:
    lo, hi = m.bounds
    H = hi[2] - lo[2]
    lv = lo[2] + (np.arange(n) + 0.5) / n * H
    secs = [section(m, z) for z in lv]
    areas = np.array([s.area for s in secs])
    if areas.max() <= 0:
        raise UnreadableFile("slicing the model gave no solid area; it may be a surface without thickness")
    # --- floor slab: the bottom slices cover most of the footprint, then the area drops to the walls
    hull_all = unary_union([s for s in secs if not s.is_empty]).convex_hull
    a0 = areas[0]
    i = 0
    while i < n and areas[i] > 0.5 * a0:
        i += 1
    rest = areas[i:] if i < n else areas[-1:]
    a_wall_guess = float(np.median(rest[rest > 0])) if np.any(rest > 0) else 0.0
    slab = bool(a0 >= 0.5 * hull_all.area and a_wall_guess > 0 and a0 > 2.5 * a_wall_guess and i < n)
    if slab:
        floor_top = _refine(m, lv[i - 1], lv[i], "max")
        j0 = i
    else:
        floor_top, j0 = float(lo[2]), 0
    # --- wall band: from floor top until the slices empty out (open top) or grow again (roof/next floor)
    band = areas[j0:]
    a_w = float(np.median(band[band > 0])) if np.any(band > 0) else 0.0
    if a_w <= 0:
        raise UnreadableFile("nothing stands up from the floor, so there are no walls to read")
    above = None
    j = j0
    while j < n and areas[j] > 0.05 * a_w and areas[j] < 2.5 * a_w:
        j += 1
    if j >= n:
        wall_top = float(hi[2])
    elif areas[j] >= 2.5 * a_w:
        wall_top = _refine(m, lv[j - 1], lv[j], "min")
        above = "roof or another floor"
    else:
        wall_top = _refine(m, lv[j - 1], lv[j], "max")
        if np.any(areas[j:] > 0.05 * a_w):
            above = "more parts above the walls (roof, upper floor or loose pieces)"
    hw = wall_top - floor_top
    if hw <= 0:
        raise UnreadableFile("could not find a wall band above the floor")
    S = {k: section(m, floor_top + f * hw) for k, f in (("L", LOW), ("M", MID), ("Hh", HIGH))}
    L, M, Hh = S["L"], S["M"], S["Hh"]
    perim = M.length if not M.is_empty else 1.0
    wall_t = float(max(1e-6, 2 * M.area / perim))
    eps = 0.15 * wall_t
    walls = _clean(L.intersection(M).intersection(Hh), eps)
    win_reg = _clean(L.intersection(Hh).difference(M), eps)
    door_reg = _clean(Hh.difference(L.union(M)), eps)
    low_reg = _clean(L.union(M).difference(Hh), eps)
    min_area = 2.0 * wall_t * wall_t

    def cover(poly, z_lo, z_hi):
        out = []
        for z, s in zip(lv, secs):
            if z_lo <= z <= z_hi:
                out.append((z, s.intersection(poly).area / max(poly.area, 1e-12)))
        return out

    step = H / n
    a = Analysis(bounds=m.bounds.copy(), levels=lv, areas=areas, slab=slab, floor_top=float(floor_top),
                 wall_top=float(wall_top), wall_t=wall_t, above_walls=above, walls=walls,
                 footprint=(secs[0] if slab else unary_union([walls, win_reg, door_reg])))
    mid_z = floor_top + MID * hw
    for p in _parts(win_reg):
        if p.area < min_area:
            continue
        below = [z for z, c in cover(p, floor_top, mid_z) if c > 0.5]
        upper = [z for z, c in cover(p, mid_z, wall_top) if c > 0.5]
        sill = (_snap(m, max(below) + step / 2, step) - floor_top) if below else None
        lintel = (_snap(m, min(upper) - step / 2, step) - floor_top) if upper else None
        a.windows.append((p, sill, lintel))
    for p in _parts(door_reg):
        if p.area < min_area:
            continue
        upper = [z for z, c in cover(p, floor_top, wall_top) if c > 0.5]
        a.doors.append((p, (_snap(m, min(upper) - step / 2, step) - floor_top) if upper else None))
    for p in _parts(low_reg):
        if p.area < min_area:
            continue
        prof = [z for z, c in cover(p, floor_top, wall_top) if c > 0.3]
        top = _refine_top(m, p, floor_top, wall_top)
        if top is None and prof:
            top = max(prof) + step / 2
        if top is not None:
            a.low.append((p, float(top - floor_top)))
    return a


def _refine_top(m, poly, z_lo, z_hi):
    """Highest mesh vertex over a low part (strictly inside it), below the wall top."""
    v = m.vertices
    sel = (v[:, 2] > z_lo + 1e-6) & (v[:, 2] < z_hi - 1e-6)
    if not np.any(sel):
        return None
    import shapely
    inner = poly.buffer(1e-6)
    pts = v[sel]
    inside = shapely.contains_xy(inner, pts[:, 0], pts[:, 1])
    return float(pts[inside, 2].max()) if np.any(inside) else None


# ---------------------------------------------------------------- pixels / pictures

def px_per_unit(bounds) -> float:
    ext = np.asarray(bounds[1]) - np.asarray(bounds[0])
    return PLAN_MAX_PX / float(max(ext[0], ext[1]))


def _to_px(g, bounds, R):
    x0, y1 = bounds[0][0], bounds[1][1]
    g = affinity.translate(g, -x0, -y1)
    g = affinity.scale(g, R, -R, origin=(0, 0))
    return affinity.translate(g, PAD_PX, PAD_PX)


def _poly_dict(p: Polygon, **extra) -> dict:
    return {"outer": [[round(x, 2), round(y, 2)] for x, y in list(p.exterior.coords)[:-1]],
            "holes": [[[round(x, 2), round(y, 2)] for x, y in list(h.coords)[:-1]] for h in p.interiors],
            **extra}


def top_view(a: Analysis, R: float) -> np.ndarray:
    """A plan-like picture of the slices: base beige, low parts grey, walls black, windows blue, doors orange."""
    from PIL import Image, ImageDraw
    ext = a.bounds[1] - a.bounds[0]
    w, h = int(ext[0] * R) + 2 * PAD_PX, int(ext[1] * R) + 2 * PAD_PX
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    def fill(g, color):
        for p in _parts(_to_px(g, a.bounds, R)):
            d.polygon([tuple(c) for c in p.exterior.coords], fill=color)
            for hole in p.interiors:
                d.polygon([tuple(c) for c in hole.coords], fill="white")

    if a.slab and a.footprint is not None:
        fill(a.footprint, (236, 226, 205))
    for p, _ in a.low:
        fill(p, (165, 165, 165))
    fill(a.walls, (25, 25, 30))
    for p, *_ in a.windows:
        fill(p, (60, 150, 220))
    for p, _ in a.doors:
        fill(p, (230, 120, 50))
    return np.array(img)


@lru_cache(maxsize=8)
def _cached(path: str, mtime: float):
    m, notes = load_mesh(path)
    return m, notes, analyze(m)


def read(path: str | Path):
    p = Path(path)
    return _cached(str(p.resolve()), p.stat().st_mtime)


def plan_image(path: str | Path) -> np.ndarray:
    m, _, a = read(path)
    return top_view(a, px_per_unit(m.bounds))


def guess_units(wall_h_units: float) -> tuple[str, float] | None:
    for name, inch, lo, hi in UNIT_GUESS:
        if lo <= wall_h_units <= hi:
            return name, inch
    return None


# ---------------------------------------------------------------- job

def detect_mesh(job: Path, params, log=print) -> dict:
    """Mesh file -> job folder (source_crop.png, polygons*.json, report.json, job.json fields)."""
    from PIL import Image
    t = time.time()
    src = Path(params.plan)
    m, notes, a = read(src)
    job.mkdir(parents=True, exist_ok=True)
    keep = job / f"original{src.suffix.lower()}"
    if src.resolve() != keep.resolve():
        shutil.copyfile(src, keep)
    R = px_per_unit(m.bounds)
    rgb = top_view(a, R)
    crop = params.crop
    ox = oy = 0.0
    clip = None
    if crop:
        from .editor import apply_crop
        rgb = apply_crop(rgb, crop)
        x0, y0, x1, y1 = (float(v) for v in crop)
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        clip, ox, oy = sbox(x0, y0, x1, y1), x0, y0
    Image.fromarray(rgb).save(job / "source_crop.png")
    Image.fromarray(top_view(a, R)).save(job / "input.png")

    def conv(g):
        g = _to_px(g, a.bounds, R)
        if clip is not None:
            g = g.intersection(clip)
        return affinity.translate(g, -ox, -oy) if (ox or oy) else g

    polys = {"wall": [], "door": [], "window": [], "furniture": [], "floor": []}
    if a.slab:
        polys["floor"] = [_poly_dict(p, from_mesh=True) for p in _parts(conv(a.footprint.buffer(0)))]
    for p in _parts(conv(a.walls)):
        polys["wall"].append(_poly_dict(p, from_mesh=True))
    for g, sill, lintel in a.windows:
        for p in _parts(conv(g)):
            polys["window"].append(_poly_dict(p, from_mesh=True,
                                              sill_px=None if sill is None else round(sill * R, 3),
                                              lintel_px=None if lintel is None else round(lintel * R, 3)))
    for g, head in a.doors:
        for p in _parts(conv(g)):
            polys["door"].append(_poly_dict(p, from_mesh=True, cut=True,
                                            head_px=None if head is None else round(head * R, 3)))
    for g, ht in a.low:
        for p in _parts(conv(g)):
            polys["furniture"].append(_poly_dict(p, from_mesh=True, height_px=round(ht * R, 3),
                                                 label="low part from the 3D file (furniture, counter or half wall)"))
    (job / "polygons.json").write_text(json.dumps(polys))
    (job / "polygons_detected.json").write_text(json.dumps(polys))

    wall_u = a.wall_top - a.floor_top
    meas = {"wall_h": wall_u * R}
    if a.slab:
        meas["floor_t"] = (a.floor_top - a.bounds[0][2]) * R
    sills = [s for _, s, _ in a.windows if s is not None]
    lints = [x for *_, x in a.windows if x is not None]
    if sills:
        meas["sill"] = float(np.median(sills)) * R
    if lints:
        meas["lintel"] = float(np.median(lints)) * R
    guess = guess_units(wall_u)
    in_per_unit = guess[1] if guess else 1.0
    params.mesh = {"px_per_unit": R, "measured_px": {k: round(v, 4) for k, v in meas.items()},
                   "unit_guess": guess[0] if guess else None,
                   "extent_units": [round(float(v), 4) for v in (a.bounds[1] - a.bounds[0])],
                   "above_walls": a.above_walls, "notes": notes,
                   "source_colors": source_colors(m.metadata.get("hp3d_color_samples"), a)}
    params.source_kind = "mesh"
    if "px_per_inch" not in params.user_set:
        params.px_per_inch = round(R / in_per_unit, 6)
        params.scale_source = (f"a guess that the 3D file is in {guess[0]} (from its wall height)" if guess
                               else "a placeholder: 1 file unit = 1 inch")
    params.measured = sorted(meas)
    apply_mesh_heights(params)
    params.detected_for = {"plan": params.plan, "crop": params.crop, "dpi": params.dpi}
    params.log(f"3D file read: {src.name} ({len(m.faces)} faces), unit guess={params.mesh['unit_guess']}")
    ext_u = params.mesh["extent_units"]
    inferred = [f"walls: {len(polys['wall'])} piece(s), about {a.wall_t:.3g} file units thick, "
                f"{wall_u:.4g} units tall (floor to wall top)"]
    inferred.append(f"floor slab: {(a.floor_top - a.bounds[0][2]):.4g} units thick" if a.slab else
                    "floor slab: NOT in the file (a base of the default thickness is added so it prints and carries the logo)")
    inferred.append(f"windows (sill + lintel): {len(polys['window'])}" + (
        f", sill {np.median(sills):.4g} / top {np.median(lints):.4g} units above the floor" if sills and lints else ""))
    inferred.append(f"door frames (opening with a header): {len(polys['door'])}; kept as openings, no solid doors")
    inferred.append(f"low parts kept from the file (furniture, counters, half walls): {len(polys['furniture'])}")
    sc = params.mesh["source_colors"]
    inferred.append("colours: kept from the file for " + ", ".join(sc) if sc else
                    "colours: none in the file (or one colour only), so the standard part colours are used")
    not_inferred = ["Openings that run the full wall height (no header) are plain gaps; they are not counted as doors.",
                    "Rebuild re-extrudes straight walls from the slices: roofs, stairs, sloped, curved or decorative "
                    "detail in the file are not carried into the editable model.",
                    "Room names and room types are not in a mesh, so none are reported."]
    if a.above_walls:
        not_inferred.append(f"Something sits above the walls ({a.above_walls}); only the lowest floor was read.")
    if not guess:
        not_inferred.append(f"Size unknown: wall height {wall_u:.4g} units doesn't match a normal ceiling in any unit, "
                            "so this is probably a scaled model. Give one real length.")
    rep = {"input": str(src), "source_kind": "mesh", "assumptions": [],
           "stages": {"mesh_import": {"status": "ok", "seconds": round(time.time() - t, 2), "faces": int(len(m.faces)),
                                      "watertight_input": bool(m.is_watertight), "extent_units": ext_u,
                                      "unit_guess": params.mesh["unit_guess"], "notes": notes,
                                      "inferred": inferred, "not_inferred": not_inferred,
                                      "polygon_counts": {k: len(v) for k, v in polys.items()}}}}
    (job / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    for line in notes + inferred:
        log("  " + line)
    params.save(job)
    return rep


def apply_mesh_heights(params) -> None:
    """Heights measured in the file follow the scale (px / px-per-inch), unless the operator set them."""
    if getattr(params, "source_kind", "plan") != "mesh" or not params.mesh or not params.px_per_inch:
        return
    meas = params.mesh.get("measured_px", {})
    vals = {k: getattr(params, k) for k in ("wall_h", "floor_t", "sill", "lintel")}
    for k, px in meas.items():
        if k not in params.user_set:
            vals[k] = round(px / params.px_per_inch, 3)
    # Keep the builder's rule 0 <= sill < lintel <= wall_h; fall back to unmeasured defaults scaled sensibly.
    if not (0 <= vals["sill"] < vals["lintel"] <= vals["wall_h"]):
        vals["lintel"] = min(vals["lintel"], vals["wall_h"])
        vals["sill"] = min(vals["sill"], vals["lintel"] * 0.45)
    for k, v in vals.items():
        setattr(params, k, v)
