"""Parametric furniture sized in real inches, shaped so it prints without supports.

The house is modelled in inches (1 model inch = 1 mm in the slicer at the
default scale). Each piece uses a real footprint and height: a single bed is
39 x 75 in, a counter is 36 in tall, a fridge is 70 in, and so on. Geometry
sits on the floor and only steps inward as it rises, or slopes at 45 degrees
or steeper, so a slicer does not need support material. Basins (sink, tub,
toilet, stove top) are open at the top.

Nothing here invents a piece. ``from_icon_mask`` emits a bed, table, chair,
sofa, desk, counter or fixture only when that symbol is drawn on the plan.
A bedroom does not grow a bed, and a kitchen does not grow a stove, just
because the room type usually has one. Two beds or two kitchens appear only
when the plan shows two of them.

Icon class ids match ``segment_cubicasa.ICON_CLASSES``. They are repeated here
so this module does not import the torch stack.
"""
from __future__ import annotations

import re

import cv2
import numpy as np
import trimesh

# Icon classes: No Icon, Window, Door, Closet, Electrical Appliance, Toilet,
# Sink, Sauna Bench, Fire Place, Bathtub, Chimney.
_CLOSET = 3
_FIXTURE_LABELS = {4: "appliance", 5: "toilet", 6: "sink", 7: "bench", 8: "fireplace", 9: "bathtub"}
# Room classes, same order as segment_cubicasa.ROOM_CLASSES.
_ROOMS = ("Background", "Outdoor", "Wall", "Kitchen", "Living Room", "Bed Room", "Bath",
          "Entry", "Railing", "Storage", "Garage", "Undefined")

DEFAULT_RGBA = (90, 160, 80, 255)  # same green as hp3d.colors furniture

# Realistic inches: width (across), depth (front to back), overall height.
SPECS: dict[str, dict] = {
    "bed_single": {"label": "single bed", "w": 39.0, "d": 75.0, "h": 44.0},
    "bed_double": {"label": "double bed", "w": 54.0, "d": 75.0, "h": 44.0},
    "bed_twins": {"label": "two single beds", "w": 80.0, "d": 75.0, "h": 44.0},
    "table": {"label": "table", "w": 36.0, "d": 60.0, "h": 30.0},
    "chair": {"label": "chair", "w": 18.0, "d": 20.0, "h": 36.0},
    "sofa": {"label": "sofa", "w": 84.0, "d": 36.0, "h": 36.0},
    "counter": {"label": "kitchen counter", "w": 48.0, "d": 25.5, "h": 36.0},
    "sink": {"label": "sink", "w": 30.0, "d": 22.0, "h": 36.0},
    "stove": {"label": "stove", "w": 30.0, "d": 25.5, "h": 36.0},
    "fridge": {"label": "fridge", "w": 36.0, "d": 30.0, "h": 70.0},
    "dishwasher": {"label": "dishwasher", "w": 24.0, "d": 24.0, "h": 34.0},
    "toilet": {"label": "toilet", "w": 18.0, "d": 28.0, "h": 30.0},
    "tub": {"label": "tub", "w": 60.0, "d": 32.0, "h": 20.0},
    "shower": {"label": "shower", "w": 36.0, "d": 36.0, "h": 84.0},
    "vanity": {"label": "vanity", "w": 36.0, "d": 22.0, "h": 34.0},
    "wardrobe": {"label": "wardrobe", "w": 48.0, "d": 24.0, "h": 84.0},
    "desk": {"label": "desk", "w": 48.0, "d": 24.0, "h": 30.0},
}

# Longest alias first so "two single beds" wins over "bed".
_ALIASES: tuple[tuple[str, str], ...] = (
    ("two singles side by side", "bed_twins"),
    ("two single beds side by side", "bed_twins"),
    ("two single beds", "bed_twins"),
    ("two twin beds", "bed_twins"),
    ("twin beds side by side", "bed_twins"),
    ("two singles", "bed_twins"),
    ("kitchen counter", "counter"),
    ("kitchen sink", "sink"),
    ("single bed", "bed_single"),
    ("twin bed", "bed_single"),
    ("double bed", "bed_double"),
    ("full bed", "bed_double"),
    ("dishwasher", "dishwasher"),
    ("refrigerator", "fridge"),
    ("wardrobe", "wardrobe"),
    ("bathtub", "tub"),
    ("shower", "shower"),
    ("vanity", "vanity"),
    ("toilet", "toilet"),
    ("counter", "counter"),
    ("fridge", "fridge"),
    ("stove", "stove"),
    ("range", "stove"),
    ("oven", "stove"),
    ("closet", "wardrobe"),
    ("couch", "sofa"),
    ("sofa", "sofa"),
    ("chair", "chair"),
    ("table", "table"),
    ("desk", "desk"),
    ("sink", "sink"),
    ("twin", "bed_single"),
    ("single", "bed_single"),
    ("double", "bed_double"),
    ("full", "bed_double"),
    ("tub", "tub"),
    ("bed", "bed_double"),
)

NAMED_COLORS = {
    "red": (200, 48, 48), "green": (90, 160, 80), "blue": (60, 120, 200),
    "white": (236, 236, 232), "black": (40, 40, 45), "orange": (230, 120, 50),
    "yellow": (230, 190, 60), "brown": (140, 90, 50), "grey": (150, 150, 150),
    "gray": (150, 150, 150), "pink": (220, 140, 170), "purple": (140, 90, 180),
    "wood": (176, 122, 74),
}

OPS = frozenset({"add_furniture", "rotate", "recolor", "resize_to", "resize_z"})

_N = r"(\d+(?:\.\d+)?)"
_ADD_AT = re.compile(
    r"^add\s+(?:a|an|the|some)?\s*(?P<kind>.+?)\s+(?:at|@)\s*(?P<x>" + _N + r")\s*,\s*(?P<y>" + _N + r")"
    + r"(?:\s*px)?(?:\s+facing\s+(?P<face>north|south|east|west))?\s*$")
_ADD_RECT = re.compile(
    r"^add\s+(?:a|an|the|some)?\s*(?P<kind>.+?)\s+(?P<x0>" + _N + r")\s*,\s*(?P<y0>" + _N + r")"
    + r"\s+(?:to|-)\s+(?P<x1>" + _N + r")\s*,\s*(?P<y1>" + _N + r")(?:\s*px)?\s*$")
_ROTATE = re.compile(
    r"^(?:rotate|turn)\s+(?:the\s+)?(?:selected|it|this)(?:\s+part)?\s+"
    r"(?:by\s+)?(?:(?P<way>left|right|clockwise|counterclockwise|ccw|cw)|"
    r"(?P<amt>\d+(?:\.\d+)?)\s*(?:degrees|deg|°)\s*"
    r"(?P<dir>clockwise|counterclockwise|ccw|cw|left|right)?)\s*$")
_RECOLOR = re.compile(
    r"^(?:recolor|recolour|paint|color|colour)\s+(?:the\s+)?(?:selected|it|this)"
    r"(?:\s+part)?\s+(?:to\s+)?(?P<col>#[0-9a-f]{6}|[a-z]+)\s*$")
_MAKE_COLOR = re.compile(
    r"^make\s+(?:the\s+)?(?:selected|it|this)(?:\s+part)?\s+(?:the\s+)?(?:colou?r\s+)?(?P<col>#[0-9a-f]{6}|[a-z]+)\s*$")
_RESIZE_TO = re.compile(
    r"^resize\s+(?:the\s+)?(?:selected|it|this)(?:\s+part)?\s+to\s+(.+?)\s+by\s+(.+)$")
_RESIZE_Z = re.compile(
    r"^(?:make|set)\s+(?:the\s+)?(?:selected|it|this)(?:\s+part)?\s+(.+?)\s+(?:tall|high)$")

_FACING = {"south": 0.0, "north": 180.0, "west": 90.0, "east": -90.0}


def kinds() -> tuple[str, ...]:
    return tuple(SPECS)


def match_kind(phrase: str) -> str | None:
    p = re.sub(r"^(?:a|an|the|some)\s+", "", phrase.strip().lower())
    for alias, kind in _ALIASES:
        if p == alias:
            return kind
    return None


def parse_color(text: str) -> tuple[tuple[int, int, int], str] | None:
    s = text.strip().lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s], s
    m = re.fullmatch(r"#([0-9a-f]{6})", s)
    if not m:
        return None
    h = m.group(1)
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return rgb, "#" + h  # type: ignore[return-value]


def explicit_color(poly: dict) -> tuple[int, int, int, int] | None:
    c = poly.get("color")
    if not c:
        return None
    rgb = [int(max(0, min(255, round(float(x))))) for x in list(c)[:3]]
    return (rgb[0], rgb[1], rgb[2], 255)


def plan_hex(poly: dict, fallback: str) -> str:
    c = explicit_color(poly)
    if c is None:
        return fallback
    return "#{:02x}{:02x}{:02x}".format(*c[:3])


# ---------------------------------------------------------------- meshes

def _box(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=(float(sx), float(sy), float(sz)))
    m.apply_translation((float(cx), float(cy), float(cz)))
    return m


def _union(parts: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    parts = [p for p in parts if p is not None and len(p.faces)]
    if not parts:
        return trimesh.Trimesh()
    if len(parts) == 1:
        return parts[0]
    try:
        merged = trimesh.boolean.union(parts, engine="manifold")
    except Exception:  # noqa: BLE001
        merged = None
    if merged is None or merged.is_empty or len(merged.faces) == 0:
        return trimesh.util.concatenate(parts)
    return merged


def _frustum(cx: float, cy: float, sx0: float, sy0: float, sx1: float, sy1: float,
             z0: float, z1: float) -> trimesh.Trimesh:
    """Convex frustum. z0 is the wider (upper) face when sx0 >= sx1."""
    def ring(sx: float, sy: float, z: float) -> list[tuple[float, float, float]]:
        return [(cx + sx / 2, cy + sy / 2, z), (cx + sx / 2, cy - sy / 2, z),
                (cx - sx / 2, cy - sy / 2, z), (cx - sx / 2, cy + sy / 2, z)]
    pts = np.array(ring(sx0, sy0, z0) + ring(sx1, sy1, z1), float)
    return trimesh.convex.convex_hull(pts)


def _snap_floor(m: trimesh.Trimesh) -> trimesh.Trimesh:
    if m.is_empty:
        return m
    z0 = float(m.bounds[0][2])
    if abs(z0) > 1e-8:
        m.apply_translation((0, 0, -z0))
    return m


def _recess(body: trimesh.Trimesh, cx: float, cy: float, sx: float, sy: float,
            z_top: float, depth: float) -> trimesh.Trimesh:
    """Open-topped rectangular basin. The cutter breaks the top face so the hole needs no support."""
    if depth <= 0.2 or sx <= 0.5 or sy <= 0.5:
        return body
    cutter = _box(cx, cy, z_top - depth / 2 + 0.35, sx, sy, depth + 0.7)
    try:
        cut = trimesh.boolean.difference([body, cutter], engine="manifold")
    except Exception:  # noqa: BLE001
        return body
    if cut is None or cut.is_empty or not cut.is_watertight:
        return body
    return cut


def _bed(w: float, d: float, h: float) -> trimesh.Trimesh:
    plat = min(16.0, max(5.0, min(h * 0.42, h - 1.2)))
    hb = min(2.2, max(1.0, d * 0.055))
    parts = [
        _box(0, 0, plat / 2, w, d, plat),
        _box(0, d / 2 - hb / 2, h / 2, w, hb, h),
    ]
    pd = min(14.0, max(2.0, d * 0.16))
    ph = min(3.2, h - plat - 0.15)
    if ph >= 0.8 and pd < d - hb - 1:
        # Overlap the mattress by a hair so the pillow welds on instead of floating.
        parts.append(_box(0, d / 2 - hb - pd / 2 - 0.15, plat + ph / 2 - 0.08, max(2.0, w - 3.0), pd, ph + 0.16))
    return _snap_floor(_union(parts))


def _twins(w: float, d: float, h: float) -> list[trimesh.Trimesh]:
    gap = min(2.0, max(0.6, w * 0.035))
    bw = (w - gap) / 2
    if bw < 6:
        return [_bed(w, d, h)]
    left, right = _bed(bw, d, h), _bed(bw, d, h)
    left.apply_translation((-(bw + gap) / 2, 0, 0))
    right.apply_translation(((bw + gap) / 2, 0, 0))
    return [left, right]


def _pedestal(w: float, d: float, h: float) -> trimesh.Trimesh:
    """Table or desk: full top, then a 45-degree skirt down to a narrower base."""
    if h <= 2.2 or w < 3 or d < 3:
        return _snap_floor(_box(0, 0, max(h, 0.8) / 2, w, d, max(h, 0.8)))
    top_t = min(1.75, max(0.9, h * 0.08))
    avail = h - top_t
    inset = min(w * 0.2, d * 0.2, avail, (w - 1.6) / 2, (d - 1.6) / 2)
    inset = max(0.0, inset)
    parts = [_box(0, 0, h - top_t / 2, w, d, top_t)]
    if inset >= 0.3 and avail > 0.4:
        drop = min(inset, avail)  # 45 degrees when drop == inset; steeper if the base runs out of height
        parts.append(_frustum(0, 0, w, d, w - 2 * inset, d - 2 * inset, h - top_t, h - top_t - drop))
        base_h = h - top_t - drop
        if base_h > 0.3:
            parts.append(_box(0, 0, base_h / 2 + 0.05, w - 2 * inset, d - 2 * inset, base_h + 0.1))
    else:
        parts.append(_box(0, 0, avail / 2, w, d, avail))
    return _snap_floor(_union(parts))


def _chair(w: float, d: float, h: float) -> trimesh.Trimesh:
    back_t = min(1.8, max(1.0, d * 0.14))
    seat_h = min(18.0, max(4.0, min(h * 0.5, h - 1.0)))
    seat_d = max(1.5, d - back_t)
    cy = -back_t / 2
    top_t = min(1.6, max(0.7, seat_h * 0.22))
    avail = seat_h - top_t
    inset = min(w * 0.16, seat_d * 0.16, avail, max(0.0, (w - 1.4) / 2), max(0.0, (seat_d - 1.4) / 2))
    parts = [
        _box(0, d / 2 - back_t / 2, h / 2, w, back_t, h),
        _box(0, cy, seat_h - top_t / 2, w, seat_d, top_t),
    ]
    if inset >= 0.25 and avail > 0.4:
        drop = min(inset, avail)
        parts.append(_frustum(0, cy, w, seat_d, max(1.0, w - 2 * inset), max(1.0, seat_d - 2 * inset),
                              seat_h - top_t, seat_h - top_t - drop))
        base_h = seat_h - top_t - drop
        if base_h > 0.3:
            parts.append(_box(0, cy, base_h / 2 + 0.05, max(1.0, w - 2 * inset), max(1.0, seat_d - 2 * inset), base_h + 0.1))
    else:
        parts.append(_box(0, cy, avail / 2, w, seat_d, avail))
    return _snap_floor(_union(parts))


def _sofa(w: float, d: float, h: float) -> trimesh.Trimesh:
    seat_h = min(18.0, max(6.0, min(h * 0.5, h - 1.0)))
    back_t = min(4.0, max(1.2, d * 0.14))
    arm_t = min(4.0, max(1.2, w * 0.07))
    arm_h = min(h, max(seat_h, h * 0.7))
    parts = [
        _box(0, 0, seat_h / 2, w, d, seat_h),
        _box(0, d / 2 - back_t / 2, h / 2, w, back_t, h),
        _box(-w / 2 + arm_t / 2, 0, arm_h / 2, arm_t, d, arm_h),
        _box(w / 2 - arm_t / 2, 0, arm_h / 2, arm_t, d, arm_h),
    ]
    ch = min(3.2, h - seat_h - 0.1)
    cw, cd = w - 2 * arm_t - 0.6, d - back_t - 1.2
    if ch >= 0.8 and cw > 4 and cd > 4:
        parts.append(_box(0, -back_t / 2 - 0.2, seat_h + ch / 2 - 0.08, cw, cd, ch + 0.16))
    return _snap_floor(_union(parts))


def _counter(w: float, d: float, h: float) -> trimesh.Trimesh:
    splash_t = min(1.4, max(0.8, d * 0.08))
    splash = min(5.0, max(0.0, h * 0.12))
    body_h = max(1.0, h - splash)
    parts = [_box(0, 0, body_h / 2, w, d, body_h)]
    if splash >= 0.6 and d > splash_t + 1:
        parts.append(_box(0, d / 2 - splash_t / 2, h / 2, w, splash_t, h))
    return _snap_floor(_union(parts))


def _with_basin(w: float, d: float, h: float, depth_frac: float) -> trimesh.Trimesh:
    splash = min(5.0, max(0.0, h * 0.12))
    body_h = max(1.0, h - splash)
    body = _counter(w, d, h)
    wall = max(1.1, min(2.6, w * 0.12, d * 0.12))
    bw, bd = w - 2 * wall, d - wall - max(wall, splash and min(1.4, d * 0.08))
    depth = min(h * depth_frac, body_h - 1.0)
    if bw < 2 or bd < 2 or depth < 0.6:
        return body
    return _snap_floor(_recess(body, 0, -0.3, bw, bd, body_h, depth))


def _stove(w: float, d: float, h: float) -> trimesh.Trimesh:
    guard = min(4.0, max(0.0, h * 0.1))
    body_h = max(1.0, h - guard)
    guard_t = min(1.4, max(0.8, d * 0.08))
    parts = [_box(0, 0, body_h / 2, w, d, body_h)]
    if guard >= 0.6:
        parts.append(_box(0, d / 2 - guard_t / 2, h / 2, w, guard_t, h))
    body = _union(parts)
    bw = min(w * 0.16, 5.0)
    bd = min(d * 0.16, 5.0)
    if bw < 1.2 or bd < 1.2 or body_h < 2:
        return _snap_floor(body)
    cutters = []
    for sx in (-0.22 * w, 0.22 * w):
        for sy in (-0.18 * d, 0.18 * d):
            cutters.append(_box(sx, sy, body_h - 0.15, bw, bd, 1.2))
    try:
        cut = trimesh.boolean.difference([body, *cutters], engine="manifold")
    except Exception:  # noqa: BLE001
        cut = None
    if cut is None or cut.is_empty or not cut.is_watertight:
        return _snap_floor(body)
    return _snap_floor(cut)


def _groove_box(w: float, d: float, h: float) -> trimesh.Trimesh:
    """A tall box with a full-height front seam (open at top and bottom, so no ledge)."""
    body = _box(0, 0, h / 2, w, d, h)
    groove = _box(0, -d / 2, h / 2, max(0.3, min(0.5, w * 0.02)), 1.4, h + 2)
    try:
        cut = trimesh.boolean.difference([body, groove], engine="manifold")
    except Exception:  # noqa: BLE001
        return body
    if cut is None or cut.is_empty or not cut.is_watertight:
        return body
    return cut


def _tub(w: float, d: float, h: float) -> trimesh.Trimesh:
    body = _box(0, 0, h / 2, w, d, h)
    wall = max(1.2, min(2.4, w * 0.08, d * 0.1))
    floor_t = max(1.2, min(2.5, h * 0.18))
    return _snap_floor(_recess(body, 0, 0, w - 2 * wall, d - 2 * wall, h, h - floor_t))


def _toilet(w: float, d: float, h: float) -> trimesh.Trimesh:
    tank_d = min(8.0, max(2.0, d * 0.30))
    bowl_h = min(16.0, max(4.0, min(h * 0.55, h - 1.0)))
    bowl_w = max(2.0, w * 0.78)
    parts = [
        _box(0, d / 2 - tank_d / 2, h / 2, w, tank_d, h),
        _box(0, -tank_d / 2, bowl_h / 2, max(2.0, w * 0.92), max(2.0, d - tank_d), bowl_h),
    ]
    body = _union(parts)
    bw = bowl_w * 0.55
    bd = max(2.0, (d - tank_d) * 0.5)
    depth = min(2.5, bowl_h * 0.28)
    return _snap_floor(_recess(body, 0, -tank_d / 2, bw, bd, bowl_h, depth))


def _shower(w: float, d: float, h: float) -> trimesh.Trimesh:
    t = min(2.4, max(1.0, min(w, d) * 0.08))
    base_h = min(4.0, max(1.2, h * 0.06))
    parts = [
        _box(0, 0, base_h / 2, w, d, base_h),
        _box(0, d / 2 - t / 2, h / 2, w, t, h),
        _box(-w / 2 + t / 2, 0, h / 2, t, d, h),
        _box(w / 2 - t / 2, 0, h / 2, t, d, h),
    ]
    return _snap_floor(_union(parts))


def build_bodies(kind: str, w: float, d: float, h: float) -> list[trimesh.Trimesh]:
    """Watertight body or bodies in local inches, centred on x/y, standing on z = 0."""
    w, d, h = float(w), float(d), float(h)
    if kind == "bed_single" or kind == "bed_double":
        return [_bed(w, d, h)]
    if kind == "bed_twins":
        return _twins(w, d, h)
    if kind == "table" or kind == "desk":
        return [_pedestal(w, d, h)]
    if kind == "chair":
        return [_chair(w, d, h)]
    if kind == "sofa":
        return [_sofa(w, d, h)]
    if kind == "counter":
        return [_counter(w, d, h)]
    if kind == "sink" or kind == "vanity":
        return [_with_basin(w, d, h, 0.22 if kind == "sink" else 0.16)]
    if kind == "stove":
        return [_stove(w, d, h)]
    if kind == "fridge" or kind == "dishwasher" or kind == "wardrobe":
        return [_groove_box(w, d, h)]
    if kind == "toilet":
        return [_toilet(w, d, h)]
    if kind == "tub":
        return [_tub(w, d, h)]
    if kind == "shower":
        return [_shower(w, d, h)]
    raise ValueError(f"unknown furniture kind {kind!r}")


def support_risk(mesh: trimesh.Trimesh, limit: float = 0.78) -> int:
    """Faces that need support: pointing down steeper than about 51 degrees, and not on the floor.

    A 45-degree slope has nz about -0.71, so ``limit`` 0.78 leaves that printable.
    Faces on the floor (z near 0) sit on the slab.
    """
    if mesh.is_empty or len(mesh.faces) == 0:
        return 0
    n = np.asarray(mesh.face_normals, float)
    z = np.asarray(mesh.triangles_center, float)[:, 2]
    return int(np.count_nonzero((n[:, 2] < -limit) & (z > 0.35)))


def _place(mesh: trimesh.Trimesh, center: np.ndarray, ang: float) -> trimesh.Trimesh:
    c, s = float(np.cos(ang)), float(np.sin(ang))
    t = np.eye(4)
    t[0, 0], t[0, 1], t[1, 0], t[1, 1] = c, -s, s, c
    t[0, 3], t[1, 3] = float(center[0]), float(center[1])
    mesh.apply_transform(t)
    return mesh


def frame_of(outer, scale: float) -> tuple[np.ndarray, float, float, float]:
    """Centre (inches), width, depth, and rotation (radians) of a footprint quad."""
    pts = np.asarray(outer, float) * float(scale)
    if len(pts) >= 4:
        e0, e1 = pts[1] - pts[0], pts[2] - pts[1]
        if float(np.hypot(e0[0], e0[1])) > 1e-6 and float(np.hypot(e1[0], e1[1])) > 1e-6:
            ang = float(np.arctan2(e0[1], e0[0]))
            return pts.mean(0), float(np.hypot(e0[0], e0[1])), float(np.hypot(e1[0], e1[1])), ang
    c = (pts.min(0) + pts.max(0)) / 2 if len(pts) else np.zeros(2)
    if len(pts) == 0:
        return c, 0.0, 0.0, 0.0
    return c, float(pts[:, 0].max() - pts[:, 0].min()), float(pts[:, 1].max() - pts[:, 1].min()), 0.0


def bodies_for(poly: dict, inches_per_px: float, wall_h: float, furniture_h: float) -> list[trimesh.Trimesh]:
    """Piece meshes in plan inches (image y grows down), z = 0 at the slab top. Empty if not parametric."""
    kind = poly.get("furn_type")
    if kind not in SPECS or len(poly.get("outer") or []) < 3:
        return []
    center, w, d, ang = frame_of(poly["outer"], inches_per_px)
    if poly.get("height_in"):
        h = float(poly["height_in"])
    elif poly.get("height_px"):
        h = float(poly["height_px"]) * float(inches_per_px)
    else:
        h = float(SPECS[kind]["h"])
    h = min(h, float(wall_h))
    if w < 1 or d < 1 or h < 1:
        return []
    out = []
    for body in build_bodies(kind, w, d, h):
        if body.is_empty:
            continue
        out.append(_place(body, center, ang))
    return out


def local_mesh(kind: str) -> trimesh.Trimesh:
    """Catalog-sized mesh, for tests and checks. Several bodies are concatenated."""
    spec = SPECS[kind]
    parts = build_bodies(kind, spec["w"], spec["d"], spec["h"])
    return parts[0] if len(parts) == 1 else trimesh.util.concatenate(parts)


# ---------------------------------------------------------------- commands

def try_parse(clause: str, parse_length) -> dict | None:
    """A furniture phrase, or None when this clause is about something else.

    ``parse_length`` is ``editor.parse_length`` (kept there so units stay in one place).
    """
    lc = clause.strip().lower()
    m = _ADD_AT.match(lc) or _ADD_RECT.match(lc)
    if m:
        kind = match_kind(m.group("kind"))
        if kind is None:
            return None
        if m.re is _ADD_AT:
            face = m.group("face")
            return {"op": "add_furniture", "furn_type": kind,
                    "at": [float(m.group("x")), float(m.group("y"))],
                    "rotation": _FACING.get(face or "", 0.0)}
        return {"op": "add_furniture", "furn_type": kind,
                "rect": [float(m.group(k)) for k in ("x0", "y0", "x1", "y1")]}
    m = _ROTATE.match(lc)
    if m:
        way = m.group("way")
        if way in ("left", "ccw", "counterclockwise"):
            deg = -90.0
        elif way in ("right", "cw", "clockwise"):
            deg = 90.0
        else:
            deg = float(m.group("amt"))
            d = m.group("dir")
            if d in ("left", "ccw", "counterclockwise"):
                deg = -abs(deg)
            elif d in ("right", "cw", "clockwise", None):
                deg = abs(deg)
        return {"op": "rotate", "degrees": deg}
    m = _RECOLOR.match(lc)
    if m:
        got = parse_color(m.group("col"))
        if got is None:
            raise ValueError(f"I don't know the colour '{m.group('col')}'. Try red, blue, wood, or #336699.")
        rgb, name = got
        return {"op": "recolor", "rgb": list(rgb), "name": name}
    m = _MAKE_COLOR.match(lc)
    if m:
        got = parse_color(m.group("col"))
        if got is None:
            return None
        rgb, name = got
        return {"op": "recolor", "rgb": list(rgb), "name": name}
    m = _RESIZE_TO.match(lc)
    if m:
        w, _ = parse_length(m.group(1))
        d, _ = parse_length(m.group(2))
        if w <= 0 or d <= 0:
            raise ValueError("resize needs both sides longer than 0")
        return {"op": "resize_to", "w": w, "d": d}
    m = _RESIZE_Z.match(lc)
    if m and re.search(r"\d", m.group(1)):
        h, _ = parse_length(m.group(1))
        if h <= 0:
            raise ValueError("height must be more than 0")
        return {"op": "resize_z", "height": h}
    return None


def quad(cx: float, cy: float, w: float, d: float, deg: float = 0.0) -> list[list[float]]:
    """Footprint around (cx, cy). ``deg`` is clockwise on the plan (y grows down)."""
    a = np.radians(deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts = []
    for sx, sy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        v = rot @ np.array([sx * w, sy * d])
        pts.append([round(float(cx + v[0]), 3), round(float(cy + v[1]), 3)])
    return pts


def _replace(polys: dict, index: int, poly: dict) -> dict:
    polys = {k: list(v) if isinstance(v, list) else v for k, v in polys.items()}
    polys["furniture"] = list(polys.get("furniture", []))
    polys["furniture"][index] = poly
    return polys


def apply_action(polys: dict, selected: tuple | None, action: dict, px_per_inch: float | None):
    """Apply one furniture action. Returns (polys, selection, message)."""
    op = action["op"]
    if op == "add_furniture":
        kind = action["furn_type"]
        spec = SPECS[kind]
        if "rect" in action:
            x0, y0, x1, y1 = action["rect"]
            x0, x1 = sorted((float(x0), float(x1)))
            y0, y1 = sorted((float(y0), float(y1)))
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                raise ValueError("that rectangle has no area")
            outer = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        else:
            if not px_per_inch:
                raise ValueError(
                    f"can't place a {spec['label']} by its real size with an unknown scale; "
                    "set px-per-inch, or give a pixel rectangle (x0,y0 to x1,y1)")
            cx, cy = action["at"]
            outer = quad(cx, cy, spec["w"] * px_per_inch, spec["d"] * px_per_inch, action.get("rotation") or 0.0)
        polys = {k: list(v) if isinstance(v, list) else v for k, v in polys.items()}
        polys["furniture"] = list(polys.get("furniture", []))
        polys["furniture"].append({
            "outer": outer, "holes": [], "added_in_app": True,
            "furn_type": kind, "label": spec["label"],
        })
        idx = len(polys["furniture"]) - 1
        return polys, ("furniture", idx), f"added {spec['label']} #{idx}"
    if selected is None or selected[0] != "furniture":
        raise ValueError(f"{op} works on a furniture piece - select one first")
    kind, index = selected
    try:
        poly = dict(polys[kind][index])
    except (KeyError, IndexError) as e:
        raise ValueError("that furniture piece is not in the model") from e
    if op == "rotate":
        deg = float(action["degrees"])
        pts = np.asarray(poly["outer"], float)
        c = (pts.min(0) + pts.max(0)) / 2
        a = np.radians(deg)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        poly["outer"] = ((pts - c) @ rot.T + c).round(3).tolist()
        poly["holes"] = [((np.asarray(h, float) - c) @ rot.T + c).round(3).tolist() for h in poly.get("holes", [])]
        polys = _replace(polys, index, poly)
        return polys, selected, f"rotated furniture #{index} by {deg:g} degrees"
    if op == "recolor":
        poly["color"] = [int(x) for x in action["rgb"]]
        polys = _replace(polys, index, poly)
        return polys, selected, f"recolored furniture #{index} {action.get('name', 'custom')}"
    if op == "resize_z":
        poly["height_in"] = round(float(action["height"]), 3)
        polys = _replace(polys, index, poly)
        return polys, selected, f"resized furniture #{index} height to {poly['height_in']:g} in"
    if op == "resize_to":
        if not px_per_inch:
            raise ValueError("can't resize by inches with an unknown scale; set px-per-inch or widen by a percent")
        pts = np.asarray(poly["outer"], float)
        if len(pts) < 3:
            raise ValueError("that piece has no footprint")
        c = pts.mean(0)
        if len(pts) >= 4 and float(np.hypot(*(pts[1] - pts[0]))) > 1e-6:
            e0, e1 = pts[1] - pts[0], pts[2] - pts[1]
            u, v = e0 / np.linalg.norm(e0), e1 / np.linalg.norm(e1)
        else:
            u, v = np.array([1.0, 0.0]), np.array([0.0, 1.0])
        local = np.stack([(pts - c) @ u, (pts - c) @ v], 1)
        span = np.ptp(local, axis=0)
        span = np.maximum(span, 1e-6)
        target = np.array([float(action["w"]), float(action["d"])]) * float(px_per_inch)
        local *= target / span
        poly["outer"] = (c + np.outer(local[:, 0], u) + np.outer(local[:, 1], v)).round(3).tolist()
        polys = _replace(polys, index, poly)
        return polys, selected, f"resized furniture #{index} to {action['w']:g} by {action['d']:g} in"
    raise ValueError(f"unknown furniture action {op}")


# ---------------------------------------------------------------- plan symbols

def _aspect_box(outer) -> tuple[float, float, float]:
    pts = np.asarray(outer, float)
    if len(pts) < 2:
        return 1.0, 0.0, 0.0
    rect = cv2.minAreaRect(pts.astype(np.float32))
    rw, rd = rect[1]
    short = max(1e-6, min(rw, rd))
    return max(rw, rd) / short, float(rw), float(rd)


def _room_name(rooms: np.ndarray | None, outer) -> str | None:
    if rooms is None or len(outer) == 0:
        return None
    c = np.asarray(outer, float).mean(0)
    x, y = int(round(c[0])), int(round(c[1]))
    h, w = rooms.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return None
    cid = int(rooms[y, x])
    if cid < 0 or cid >= len(_ROOMS):
        return None
    return _ROOMS[cid]


def classify_label(label: str, outer, rooms: np.ndarray | None = None) -> str | None:
    """Parametric kind for one detected icon, or None to keep a plain block.

    Only the symbol's own shape (and, for a sink, the room it sits in) is used.
    Room type never creates a piece that the icon mask did not already mark.
    """
    aspect, _, _ = _aspect_box(outer)
    if label == "toilet":
        return "toilet"
    if label == "sink":
        if _room_name(rooms, outer) == "Bath" and aspect >= 1.5:
            return "vanity"
        return "sink"
    if label == "bathtub":
        return "shower" if aspect < 1.35 else "tub"
    if label == "appliance":
        if aspect >= 1.55:
            return "stove"
        if aspect <= 1.22:
            return "fridge"
        return "dishwasher"
    return None


def _blobs(mask: np.ndarray, min_frac: float, min_px: int, max_frac: float | None = None) -> list[np.ndarray]:
    area = mask.size
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8))
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < max(min_px, min_frac * area):
            continue
        if max_frac is not None and a > max_frac * area:
            continue
        ys, xs = np.nonzero(lab == i)
        out.append(np.stack([xs, ys], 1).astype(np.float32))
    return out


def _record(pts: np.ndarray, label: str, kind: str | None) -> dict:
    rect = cv2.minAreaRect(pts.astype(np.float32))
    box = cv2.boxPoints(rect)
    e0, e1 = box[1] - box[0], box[2] - box[1]
    cross = float(e0[0] * e1[1] - e0[1] * e1[0])
    if cross < 0:
        box = box[[0, 3, 2, 1]]
    rec = {"outer": [[round(float(x), 1), round(float(y), 1)] for x, y in box],
           "holes": [], "label": label, "from_plan_icon": True}
    if kind:
        rec["furn_type"] = kind
        rec["label"] = SPECS[kind]["label"]
    return rec


def from_icon_mask(icons: np.ndarray, rooms: np.ndarray | None = None, rgb: np.ndarray | None = None,
                   min_frac: float = 0.0004, min_px: int = 40) -> list[dict]:
    """Furniture polygons for symbols actually present on this plan.

    CubiCasa icon pixels become toilets, sinks, tubs, showers, appliances and
    (when the closet mark is furniture-sized) wardrobes. Drawn bed, table,
    chair, sofa, desk and counter marks are read from the lines. Room colours
    alone contribute nothing: two kitchens, or a bedroom with no bed drawn,
    add no pieces.
    """
    found = []
    if icons is not None and icons.size:
        for cid, label in _FIXTURE_LABELS.items():
            for pts in _blobs(icons == cid, min_frac, min_px):
                outer = _record(pts, label, None)["outer"]
                kind = classify_label(label, outer, rooms)
                found.append(_record(pts, label, kind))
        for pts in _blobs(icons == _CLOSET, min_frac, min_px, max_frac=0.08):
            found.append(_record(pts, "wardrobe", "wardrobe"))
    if rgb is not None:
        found.extend(drawn_symbols(rgb))
    return found


def _ink(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.int16)
    dark = a.min(axis=2) < 80
    plain = (a.max(axis=2) - a.min(axis=2)) < 45
    return np.where(dark & plain, np.uint8(255), np.uint8(0))


def _patch(ink: np.ndarray, box: np.ndarray, w: int, d: int) -> np.ndarray | None:
    if w < 8 or d < 8:
        return None
    src = box.astype(np.float32)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, d - 1], [0, d - 1]], np.float32)
    try:
        warp = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(ink, warp, (w, d), flags=cv2.INTER_NEAREST)
    except cv2.error:
        return None


def _end_score(profile: np.ndarray) -> tuple[float, float, int]:
    """(strong end, weak end, index of the strong end). Uses the peak in each end band,
    so a single pillow line is not diluted by the empty rows around it."""
    n = len(profile)
    band = max(2, int(round(n * 0.30)))
    if n < band * 2 + 1:
        return 0.0, 0.0, 0
    a, b = float(profile[:band].max()), float(profile[-band:].max())
    if a >= b:
        return a, b, 0
    return b, a, 1


def drawn_symbols(rgb: np.ndarray) -> list[dict]:
    """Line symbols only. A filled wall, an empty rectangle, or a room wash is not furniture."""
    if rgb is None or rgb.size == 0:
        return []
    ink = _ink(rgb)
    if int(ink.sum()) < 255 * 20:
        return []
    cnts, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = float(ink.size)
    rects = []
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < 0.0015 * img_area or area > 0.18 * img_area:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rd), _ang = rect
        if rw < 12 or rd < 12:
            continue
        box = cv2.boxPoints(rect)
        if box[:, 0].min() <= 1 or box[:, 1].min() <= 1 or box[:, 0].max() >= ink.shape[1] - 2 or box[:, 1].max() >= ink.shape[0] - 2:
            continue
        short, long = min(rw, rd), max(rw, rd)
        aspect = long / max(short, 1e-6)
        # Orient the patch so its height is the long side when the symbol is long.
        e0, e1 = box[1] - box[0], box[2] - box[1]
        if np.hypot(*e0) < np.hypot(*e1):
            box = np.roll(box, -1, axis=0)
            e0, e1 = box[1] - box[0], box[2] - box[1]
        if float(e0[0] * e1[1] - e0[1] * e1[0]) < 0:
            box = box[[0, 3, 2, 1]]
        pw, pd = int(round(np.hypot(*(box[1] - box[0])))), int(round(np.hypot(*(box[2] - box[1]))))
        patch = _patch(ink, box, max(pw, 8), max(pd, 8))
        if patch is None:
            continue
        m = max(3, int(0.08 * min(patch.shape)))
        if patch.shape[0] <= 2 * m + 4 or patch.shape[1] <= 2 * m + 4:
            continue
        inner = (patch[m:-m, m:-m] > 0).astype(np.float32)
        if float(inner.mean()) > 0.4:
            continue  # filled (a wall or a solid hatch), not a symbol
        row_s, row_w, row_i = _end_score(inner.mean(1))
        col_s, col_w, col_i = _end_score(inner.mean(0))
        mid_r = inner[int(inner.shape[0] * 0.35):int(inner.shape[0] * 0.65),
                      int(inner.shape[1] * 0.35):int(inner.shape[1] * 0.65)]
        mid = float(mid_r.mean()) if mid_r.size else 0.0
        rects.append({
            "box": box, "cx": float(cx), "cy": float(cy), "rw": float(rw), "rd": float(rd),
            "aspect": float(aspect), "area": area, "inner": inner, "mid": mid,
            "row_s": row_s, "row_w": row_w, "row_i": row_i,
            "col_s": col_s, "col_w": col_w, "col_i": col_i,
            # The patch is oriented with the long side along x, so short ends are columns
            # and a back running the long way shows up in the rows.
            "long_on_x": True,
        })
    chair_ids = {id(r) for r in rects if _is_chair(r, img_area)}
    out = []
    for r in rects:
        kind = _drawn_kind(r, rects, chair_ids, img_area)
        if kind is None:
            continue
        out.append(_drawn_record(r, kind))
    return out


def _bar(strong: float, weak: float, mid: float) -> bool:
    return strong >= 0.35 and strong > weak + 0.2 and strong > mid + 0.15


def _is_chair(r: dict, img_area: float) -> bool:
    if r["area"] > 0.02 * img_area or r["aspect"] > 1.55:
        return False
    return _bar(r["row_s"], r["row_w"], r["mid"]) or _bar(r["col_s"], r["col_w"], r["mid"])


def _near_chair(r: dict, rects: list[dict], chair_ids: set[int]) -> int:
    n = 0
    reach = 0.55 * (r["rw"] + r["rd"])
    for c in rects:
        if id(c) not in chair_ids or c is r:
            continue
        if abs(c["cx"] - r["cx"]) + abs(c["cy"] - r["cy"]) < reach + 0.55 * (c["rw"] + c["rd"]):
            n += 1
    return n


def _drawn_kind(r: dict, rects: list[dict], chair_ids: set[int], img_area: float) -> str | None:
    # Long side of the patch is x: a bar across a short end is a column peak,
    # a bar along a long side is a row peak.
    short_end = _bar(r["col_s"], r["col_w"], r["mid"])
    long_side = _bar(r["row_s"], r["row_w"], r["mid"])
    if id(r) in chair_ids:
        return "chair"
    if short_end and 1.65 <= r["aspect"] <= 2.35:
        return "bed_single"
    if short_end and 1.22 <= r["aspect"] < 1.65:
        return "bed_double"
    if 0.85 <= r["aspect"] < 1.25 and long_side and _center_divider(r):
        return "bed_twins"
    if long_side and r["aspect"] >= 2.15 and not short_end:
        return "sofa"
    empty = r["col_s"] < 0.2 and r["row_s"] < 0.2 and r["mid"] < 0.08
    if empty:
        n = _near_chair(r, rects, chair_ids)
        if n == 1 and r["aspect"] >= 1.55:
            return "desk"
        if n >= 1:
            return "table"
    # A long outline with a compact bowl (a counter), not a full-length back.
    if r["aspect"] >= 2.5 and 0.02 < float(r["inner"].mean()) < 0.2 and not long_side and not short_end:
        return "counter"
    return None


def _center_divider(r: dict) -> bool:
    """A line down the middle, the gap between two singles drawn as one symbol."""
    inner = r["inner"]
    h, w = inner.shape
    if w < 8:
        return False
    gap = inner[:, int(w * 0.42):int(w * 0.58)]
    return float(gap.mean()) > 0.04 and r["row_w"] < 0.25


def _drawn_record(r: dict, kind: str) -> dict:
    box = np.asarray(r["box"], float)
    e0 = box[1] - box[0]  # long side of the patch
    e1 = box[2] - box[1]  # short side
    if kind == "sofa" or kind == "bed_twins":
        toward = e1 if r["row_i"] == 1 else -e1
        depth, width = float(np.hypot(*e1)), float(np.hypot(*e0))
    elif kind in ("bed_single", "bed_double"):
        toward = e0 if r["col_i"] == 1 else -e0
        depth, width = float(np.hypot(*e0)), float(np.hypot(*e1))
    elif kind == "chair":
        if r["col_s"] >= r["row_s"]:
            toward = e0 if r["col_i"] == 1 else -e0
        else:
            toward = e1 if r["row_i"] == 1 else -e1
        depth, width = float(np.hypot(*e0)), float(np.hypot(*e1))
        if depth < width:
            depth, width = width, depth
    else:
        toward = e1
        width, depth = float(np.hypot(*e0)), float(np.hypot(*e1))
    v = toward / max(np.linalg.norm(toward), 1e-6)
    u = np.array([v[1], -v[0]])  # +X so that +X cross +Y stays positive
    c = box.mean(0)
    outer = []
    for sx, sy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        p = c + u * (sx * width) + v * (sy * depth)
        outer.append([round(float(p[0]), 1), round(float(p[1]), 1)])
    spec = SPECS[kind]
    return {"outer": outer, "holes": [], "label": spec["label"], "furn_type": kind, "from_plan_symbol": True}
