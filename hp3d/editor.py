"""In-app editing core for the house-plans-to-3d GUI (no tkinter here, so it is testable headless).

A *job* is a folder under out/<name>/ holding:
  job.json                 editable parameters (plan, crop, scale, heights) + edit history
  source_crop.png          the cropped plan that detection actually ran on
  polygons_detected.json   polygons as detected (never changed after detection)
  polygons.json            polygons as edited in the app (what 3D is built from)
  model.stl/.3mf/.glb, part_*.stl, preview.png, plan_overlay.png, report.json

Detection (segmentation) is only re-run when the plan or crop changes. Parameter
or polygon edits rebuild 3D from polygons.json, which takes seconds.
All polygon coordinates are pixels of source_crop.png (x right, y down).
"""
from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import furniture

KINDS = ("wall", "door", "window", "furniture")
BUILD_SKIP = {"build3d[trimesh]", "repair_export[pymeshfix]"}  # the editor builds 3D itself
DEFAULTS = {"wall_h": 96.0, "floor_t": 4.0, "sill": 36.0, "lintel": 80.0, "furniture_h": 30.0}
HEIGHTS = ("wall_h", "floor_t", "sill", "lintel")


@dataclass
class JobParams:
    plan: str = ""
    crop: list | None = None            # [x0, y0, x1, y1] in original-plan pixels, or None = whole plan
    crop_source: str = "none"           # "none" | "manual" | "auto-green (adjustable)"
    px_per_inch: float | None = None    # None = unknown scale (1 px = 1 in placeholder)
    dpi: float = 100.0
    wall_h: float = DEFAULTS["wall_h"]
    floor_t: float = DEFAULTS["floor_t"]
    sill: float = DEFAULTS["sill"]
    lintel: float = DEFAULTS["lintel"]
    furniture_h: float = DEFAULTS["furniture_h"]   # used only for furniture with no measured height
    print_mm_per_unit: float = 1.0      # printed mm per model inch (STL read as mm); sizes the underside logo
    skip: list = field(default_factory=list)
    user_set: list = field(default_factory=list)   # which params Ari set himself (others are defaults)
    edits: list = field(default_factory=list)      # in-app edit log
    detected_for: dict | None = None               # plan+crop that polygons_detected.json came from
    source_kind: str = "plan"                      # "plan" (image/PDF) | "mesh" (STL/OBJ/3MF/GLB...)
    mesh: dict | None = None                       # what the 3D-file reader measured (see mesh_import)
    measured: list = field(default_factory=list)   # params read from the file itself (not defaults)
    quiz: dict = field(default_factory=dict)       # question id -> {"answer", "how"} (see hp3d.quiz)
    scale_source: str = ""                         # where a not-user-set scale came from (e.g. "the DXF file's units")
    find_fixtures: bool = False                    # opt-in: fixture blocks from CubiCasa icons (often wrong)

    def save(self, job: Path) -> None:
        job.mkdir(parents=True, exist_ok=True)
        (job / "job.json").write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def load(cls, job: Path) -> "JobParams":
        p = job / "job.json"
        if not p.exists():
            return cls()
        d = json.loads(p.read_text())
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def log(self, what: str) -> None:
        self.edits.append({"time": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "edit": what})

    def set(self, name: str, value) -> None:
        old = getattr(self, name)
        setattr(self, name, value)
        if name not in self.user_set:
            self.user_set.append(name)
        self.log(f"set {name}: {old} -> {value}")


# ---------------------------------------------------------------- crop

def find_green_outline(rgb: np.ndarray, margin: int = 15, min_run: int = 30) -> list | None:
    """Bounding box of a saturated green outline (e.g. the unit boundary on living.png),
    padded by `margin` px so exterior walls drawn just outside the line are kept.
    Returns None when no clear green outline is present."""
    a = rgb.astype(int)
    m = (a[..., 1] > 150) & (a[..., 0] < 120) & (a[..., 2] < 120)
    cols, rows = np.nonzero(m.sum(0) > min_run)[0], np.nonzero(m.sum(1) > min_run)[0]
    if len(cols) < 2 or len(rows) < 2:
        return None
    h, w = m.shape
    return [max(0, int(cols.min()) - margin), max(0, int(rows.min()) - margin),
            min(w, int(cols.max()) + margin + 1), min(h, int(rows.max()) + margin + 1)]


def apply_crop(rgb: np.ndarray, crop: list | None) -> np.ndarray:
    if not crop:
        return rgb
    x0, y0, x1, y1 = (int(round(v)) for v in crop)
    h, w = rgb.shape[:2]
    x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
    y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
    if x1 - x0 < 16 or y1 - y0 < 16:
        raise ValueError(f"crop {crop} is too small (needs at least 16x16 px inside the plan)")
    return rgb[y0:y1, x0:x1].copy()


# ---------------------------------------------------------------- polygons

def load_polys(job: Path) -> dict:
    p = job / "polygons.json"
    d = json.loads(p.read_text()) if p.exists() else {}
    out = {k: d.get(k, []) for k in KINDS}
    out.update({k: v for k, v in d.items() if k not in KINDS})   # e.g. "floor" footprint from a 3D file
    return out


def save_polys(job: Path, polys: dict) -> None:
    (job / "polygons.json").write_text(json.dumps(polys))


def counts(polys: dict) -> dict:
    """Part counts for the report (furniture only listed when the plan/file has some)."""
    return {k: len(polys.get(k, [])) for k in KINDS if k != "furniture" or polys.get(k)}


def list_parts(polys: dict) -> list[dict]:
    out = []
    for k in KINDS:
        for i, p in enumerate(polys.get(k, [])):
            pts = np.asarray(p["outer"], float)
            if len(pts) == 0:
                continue
            x0, y0 = pts.min(0); x1, y1 = pts.max(0)
            title = p.get("label") if p.get("furn_type") else k
            out.append({"kind": k, "index": i, "bbox": [float(x0), float(y0), float(x1), float(y1)],
                        "label": f"{title} #{i}  {x1 - x0:.0f}x{y1 - y0:.0f}px @ ({x0:.0f},{y0:.0f})"})
    return out


def move_part(polys: dict, kind: str, index: int, dx_px: float, dy_px: float) -> dict:
    polys = copy.deepcopy(polys)
    p = polys[kind][index]
    p["outer"] = [[x + dx_px, y + dy_px] for x, y in p["outer"]]
    p["holes"] = [[[x + dx_px, y + dy_px] for x, y in h] for h in p.get("holes", [])]
    return polys


def delete_part(polys: dict, kind: str, index: int) -> dict:
    polys = copy.deepcopy(polys)
    del polys[kind][index]
    return polys


def add_rect(polys: dict, kind: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    x0, x1 = sorted((float(x0), float(x1))); y0, y1 = sorted((float(y0), float(y1)))
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise ValueError("rectangle needs non-zero width and height")
    polys = copy.deepcopy(polys)
    polys.setdefault(kind, []).append({"outer": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "holes": [], "added_in_app": True})
    return polys


def resize_part(polys: dict, kind: str, index: int, factor_x: float, factor_y: float) -> dict:
    """Scale one part about its own centre (e.g. widen a window)."""
    polys = copy.deepcopy(polys)
    p = polys[kind][index]
    pts = np.asarray(p["outer"], float)
    c = (pts.min(0) + pts.max(0)) / 2
    f = np.array([factor_x, factor_y])
    p["outer"] = ((pts - c) * f + c).tolist()
    p["holes"] = [((np.asarray(h, float) - c) * f + c).tolist() for h in p.get("holes", [])]
    return polys


# ---------------------------------------------------------------- plain-language commands

_NUM = r"(\d+(?:\.\d+)?)"
_LEN = re.compile(r"""^\s*(?:(\d+(?:\.\d+)?)\s*(?:'|ft|foot|feet)\s*(?:(\d+(?:\.\d+)?)\s*(?:"|''|in|inch|inches)?)?
                       |(\d+(?:\.\d+)?)\s*(?:"|in|inch|inches)
                       |(\d+(?:\.\d+)?)\s*(?:cm|centimet(?:er|re)s?)
                       |(\d+(?:\.\d+)?)\s*(?:mm|millimet(?:er|re)s?)
                       |(\d+(?:\.\d+)?)\s*(px|pixels?))\s*$""", re.X)


def parse_length(s: str, allow_px: bool = False) -> tuple[float, str]:
    """'8 feet' / "8'6\"" / '96 inches' / '20 cm' -> (inches, 'in'); '12 px' -> (12, 'px').
    A bare number with no unit is rejected so dimensions are never guessed."""
    m = _LEN.match(s.strip().lower())
    if not m:
        raise ValueError(f"could not read a length with a unit from '{s.strip()}' (say e.g. '8 feet', '96 inches', \"8'6\\\"\")")
    ft, ftin, inch, cm, mm, px, _ = m.groups()
    if ft is not None:
        return float(ft) * 12 + float(ftin or 0), "in"
    if inch is not None:
        return float(inch), "in"
    if cm is not None:
        return float(cm) / 2.54, "in"
    if mm is not None:
        return float(mm) / 25.4, "in"
    if not allow_px:
        raise ValueError("pixels are only allowed for moves; give heights in feet/inches/cm")
    return float(px), "px"


_DIRS = {"left": (-1, 0), "west": (-1, 0), "right": (1, 0), "east": (1, 0),
         "up": (0, -1), "north": (0, -1), "down": (0, 1), "south": (0, 1)}
_PARAM_WORDS = [
    ("wall_h", r"(?:walls?|ceiling)(?:\s+height)?|wall\s*h|height\s+of\s+(?:the\s+)?walls"),
    ("floor_t", r"floor(?:\s+(?:thickness|slab))?|slab(?:\s+thickness)?"),
    ("sill", r"(?:window\s+)?sills?(?:\s+height)?"),
    ("lintel", r"lintels?|headers?|window\s+tops?|top\s+of\s+(?:the\s+)?windows?"),
    ("furniture_h", r"furniture(?:\s+(?:blocks?|height))?"),
]
HELP = """Phrases I understand (one per line, or join with 'and' / ';'):
  make the walls 8 feet | wall height 96 inches | ceiling 240 cm   (metres are not read)
  floor 4 inches | slab thickness 3 in
  sills at 36 inches | lintels at 80 inches | window tops 7 feet
  set scale to 1.0 | 1.85 px per inch
  (up/north = toward the top of the plan image)
  move selected 12 inches left | move it 10 px down | move selected 1 foot north
  delete selected
  add wall 100,40 to 300,52 | add window x0,y0 to x1,y1 | add door ... (pixels of the cropped plan)
     (an added door cuts a door-frame opening; add furniture ... adds a plain block)
  add a single bed at 120,80 | add a double bed at 120,80 | add two single beds at 120,80
  add a table / chair / sofa / desk / kitchen counter / sink / stove / fridge / dishwasher
  add a toilet / tub / shower / vanity / wardrobe   (at x,y uses a real size and needs a scale;
     or "add a chair 10,10 to 28,30" in pixels). facing north/south/east/west optional
  rotate selected 90 degrees | turn selected left | recolor selected red | paint selected #336699
  resize selected to 39 inches by 75 inches | make selected 24 inches tall
  furniture 30 inches   (height of plain blocks that have no measured height)
  widen selected 20% | narrow selected 10% | make selected 2x taller
  undo | redo
  rebuild | help
Every length needs a unit; a bare number is rejected rather than guessed."""


def parse_command(text: str) -> list[dict]:
    """Deterministic phrase -> action list. Raises ValueError (with the offending phrase) when
    any clause is not understood, so nothing is applied half-guessed."""
    actions: list[dict] = []
    clauses = [c.strip(" .!") for c in re.split(r"\s*(?:;|\n|\band\b|\bthen\b)\s*", text.strip(), flags=re.I) if c.strip(" .!")]
    if not clauses:
        raise ValueError("nothing to do - type a phrase (try 'help')")
    for c in clauses:
        raw = c.lower().strip()
        raw = re.sub(r"^(?:please\s+)?(?:can you\s+)?", "", raw)
        lc = re.sub(r"\s+(?:tall|high|thick|deep)$", "", raw)
        if lc in ("help", "?"):
            actions.append({"op": "help"}); continue
        if lc in ("rebuild", "rebuild it", "rebuild the model", "update", "refresh"):
            actions.append({"op": "rebuild"}); continue
        if lc in ("undo", "undo that", "undo last", "undo it"):
            actions.append({"op": "undo"}); continue
        if lc in ("redo", "redo that", "redo last", "redo it"):
            actions.append({"op": "redo"}); continue
        m = re.match(r"^(?:set\s+)?(?:the\s+)?scale\s*(?:to|=|is|at|of)?\s*" + _NUM + r"(?:\s*(?:px|pixels?)\s*(?:/|per)\s*(?:in|inch))?$", lc) \
            or re.match(r"^(?:set\s+)?" + _NUM + r"\s*(?:px|pixels?)\s*(?:/|per)\s*(?:in|inch)$", lc) \
            or re.match(r"^(?:set\s+)?px[-\s]?per[-\s]?inch\s*(?:to|=)?\s*" + _NUM + "$", lc)
        if m:
            v = float(m.group(1))
            if v <= 0:
                raise ValueError(f"'{c}': scale must be > 0")
            actions.append({"op": "set", "param": "px_per_inch", "value": v}); continue
        m = re.match(r"^(?:delete|remove)\s+(?:the\s+)?(?:selected|selection|it|this|that)(?:\s+part)?$", lc)
        if m:
            actions.append({"op": "delete"}); continue
        m = re.match(r"^(?:move|shift|nudge)\s+(?:the\s+)?(?:selected|selection|it|this|that)(?:\s+part)?\s+(.+?)\s+(left|right|up|down|north|south|east|west)$", lc)
        if m:
            amt, unit = parse_length(m.group(1), allow_px=True)
            dx, dy = _DIRS[m.group(2)]
            actions.append({"op": "move", "dx": dx * amt, "dy": dy * amt, "unit": unit}); continue
        m = re.match(r"^add\s+(?:a\s+|an\s+)?(wall|window|door|furniture)\s+(?:from\s+|at\s+)?" + _NUM + r"\s*,\s*" + _NUM +
                     r"\s+(?:to|-)\s+" + _NUM + r"\s*,\s*" + _NUM + r"(?:\s*px)?$", lc)
        if m:
            actions.append({"op": "add", "kind": m.group(1), "rect": [float(m.group(i)) for i in range(2, 6)]}); continue
        m = re.match(r"^(widen|narrow|lengthen|shorten)\s+(?:the\s+)?(?:selected|it|this)(?:\s+part)?\s+(?:by\s+)?" + _NUM + r"\s*%$", lc)
        if m:
            p = float(m.group(2)) / 100
            f = 1 + p if m.group(1) in ("widen", "lengthen") else 1 - p
            if f <= 0:
                raise ValueError(f"'{c}': that would shrink the part to nothing")
            actions.append({"op": "resize", "fx": f, "fy": 1.0, "axis": "long"}); continue
        m = re.match(r"^make\s+(?:the\s+)?(?:selected|it|this)\s+" + _NUM + r"\s*x\s+(wider|taller|longer|deeper)$", lc)
        if m:
            f = float(m.group(1))
            actions.append({"op": "resize", "fx": f if m.group(2) in ("wider", "longer") else 1.0,
                            "fy": f if m.group(2) in ("taller", "deeper") else 1.0, "axis": "xy"}); continue
        hit = False
        for param, words in _PARAM_WORDS:
            m = re.match(r"^(?:make|set|change|put)?\s*(?:the\s+)?(?:" + words + r")\s*(?:height\s+|thickness\s+)?(?:to|=|at|is|are|of)?\s*(?:be\s+)?(.+)$", lc)
            if m and re.search(r"\d", m.group(1)):
                v, _ = parse_length(m.group(1))
                actions.append({"op": "set", "param": param, "value": round(v, 3)}); hit = True; break
        if hit:
            continue
        furn = furniture.try_parse(raw, parse_length)
        if furn is not None:
            actions.append(furn)
            continue
        raise ValueError(f"I didn't understand '{c}'. Nothing was changed. Type 'help' for phrases that work.")
    return actions


# ---------------------------------------------------------------- detect / rebuild

def detect(job: Path, params: JobParams, log=print) -> dict:
    """Crop the plan and run segmentation (slow). Writes polygons_detected.json + polygons.json."""
    from . import pipeline
    from .raster import load_plan
    from PIL import Image

    from .raster import file_kind
    if file_kind(params.plan) == "mesh":
        from . import mesh_import
        log(f"reading the 3D file {Path(params.plan).name} ...")
        return mesh_import.detect_mesh(job, params, log=log)
    params.source_kind, params.scale_source = "plan", ""
    plan = load_plan(params.plan, dpi=params.dpi)
    if not plan.is_pdf and not params.px_per_inch and not plan.px_per_inch:
        log("No scale yet. Dimension labels on the plan will be read before any size is assumed.")
    if plan.px_per_inch and not params.px_per_inch:
        params.px_per_inch = round(plan.px_per_inch, 6)
        params.scale_source = "the DXF file's own units"
        params.log(f"scale read from the DXF file's units: {params.px_per_inch:.4f} px/in (verify one wall)")
    rgb = apply_crop(plan.rgb, params.crop)
    job.mkdir(parents=True, exist_ok=True)
    src = job / "source_crop.png"
    Image.fromarray(rgb).save(src)
    log(f"detecting on {src.name} ({rgb.shape[1]}x{rgb.shape[0]} px, crop={params.crop or 'whole plan'}) ...")
    # PDFs keep their text layer only uncropped, so let the pipeline read the PDF itself then.
    inp = Path(params.plan) if (plan.is_pdf and not params.crop) else src
    params.measured, params.mesh = [], None
    rep = pipeline.run(inp, job, params.px_per_inch, params.dpi, params.wall_h, set(params.skip) | BUILD_SKIP,
                       fixtures=params.find_fixtures)
    for k, v in rep["stages"].items():
        if k not in BUILD_SKIP:
            log(f"  {v['status']:>7}  {k}" + (f" -> {v.get('error')}" if v["status"] == "error" else ""))
    if not (job / "polygons.json").exists():
        raise RuntimeError("segmentation produced no polygons; see the log/report.json")
    if inp != src:
        Image.fromarray(plan.rgb).save(src)
    (job / "polygons_detected.json").write_text((job / "polygons.json").read_text())
    scale = rep.get("scale") or {}
    if scale.get("measured") and "px_per_inch" not in params.user_set:
        params.px_per_inch = scale["px_per_inch"]
        params.scale_source = scale.get("source") or ""
        if "px_per_inch" not in params.measured:
            params.measured.append("px_per_inch")
        params.log(f"scale read from dimension labels: {params.px_per_inch:.4f} px/in")
    elif not params.px_per_inch and scale.get("px_per_inch"):
        params.px_per_inch = scale["px_per_inch"]
        params.scale_source = scale.get("source") or "estimated from printed room dimensions"
        params.log(f"scale {params.px_per_inch:.4f} px/in from {params.scale_source} (verify before printing)")
    params.detected_for = {"plan": params.plan, "crop": params.crop, "dpi": params.dpi}
    if params.find_fixtures:
        params.detected_for["fixtures"] = True
    params.log(f"detection run (crop={params.crop}, source={params.crop_source})")
    params.save(job)
    return rep


def needs_detection(job: Path, params: JobParams) -> bool:
    want = {"plan": params.plan, "crop": params.crop, "dpi": params.dpi}
    if params.find_fixtures:
        want["fixtures"] = True
    return (not (job / "polygons.json").exists()) or params.detected_for != want


def assumptions(params: JobParams) -> list[str]:
    a = []
    mesh = params.source_kind == "mesh" and params.mesh
    if mesh and "px_per_inch" not in params.user_set:
        g = params.mesh.get("unit_guess")
        a.append(f"Size GUESSED: the 3D file has no units; read as {g} because the wall height fits that unit. "
                 "Give one real length to fix it." if g else
                 "Size UNKNOWN: the 3D file has no units and its wall height fits no normal ceiling, so 1 file unit = "
                 "1 inch is a placeholder. Give one real length.")
    elif not params.px_per_inch:
        a.append("Scale UNKNOWN: 1 px = 1 inch placeholder. The model size is wrong until px-per-inch is set.")
    elif "px_per_inch" in params.measured:
        a.append(f"Scale {params.px_per_inch:g} px/in was read from the plan's dimension labels.")
    elif "px_per_inch" not in params.user_set and params.scale_source:
        a.append(f"Scale {params.px_per_inch:g} px/in comes from {params.scale_source}; verify one known wall.")
    elif "px_per_inch" not in params.user_set:
        a.append(f"Scale {params.px_per_inch} px/in was estimated, not set by you; verify one known wall.")
    names = {"wall_h": "Wall height", "floor_t": "Floor thickness", "sill": "Window sill", "lintel": "Window lintel"}
    for k, n in names.items():
        if k in params.user_set:
            continue
        if k in params.measured:
            size_note = (" at the size you gave." if "px_per_inch" in params.user_set else
                         " at the size read from the plan." if "px_per_inch" in params.measured else
                         " (right once the size is right).")
            a.append(f"{n} {getattr(params, k)} in was measured from the 3D file" + size_note)
        else:
            a.append(f"{n} {getattr(params, k)} in is a default, not read from the plan.")
    if mesh:
        if "floor_t" not in params.measured:
            a.append("The 3D file had no floor slab; a base was added so it prints and carries the logo.")
        if params.mesh.get("above_walls"):
            a.append(f"Only the lowest floor was read; the file has {params.mesh['above_walls']} above the walls.")
    for qid, v in (params.quiz or {}).items():
        if v.get("how") == "skipped":
            a.append(f"Question '{qid}' was skipped; the value shown for it is still a guess.")
    if params.crop_source.startswith("auto"):
        a.append(f"Crop {params.crop} was auto-detected from the green outline; check it covers the unit's exterior walls.")
    a.append("Open-top model (no roof/ceiling). Doors are openings, not solid parts; the wall right around "
             "each doorway is the orange door-frame part. Colours are in model.3mf / model.glb (STL has none).")
    return a


def rebuild(job: Path, params: JobParams, log=print) -> dict:
    """Fast path: build 3D from polygons.json with current params, repair, export, preview, report."""
    import trimesh
    from . import build3d, repair
    from .preview import render

    polys = load_polys(job)
    ppi = params.px_per_inch or 1.0
    t = time.time()
    parts = build3d.build(polys, 1.0 / ppi, wall_h=params.wall_h, floor_t=params.floor_t,
                          sill=params.sill, lintel=params.lintel, furniture_h=params.furniture_h,
                          frame_trim=build3d.FRAME_TRIM)
    if not parts:
        raise RuntimeError("nothing to build: no wall/window polygons")
    fixed = {k: repair.repair(m) for k, m in parts.items()}
    from . import brand
    logo = brand.apply_underside_logo(fixed, mm_per_unit=params.print_mm_per_unit)
    log(f"underside logo: {logo['status']} " + (f"{logo['variant']} {logo['width_mm']} mm" if logo["status"] == "ok" else logo.get("reason", "")))
    for old in job.glob("part_*.stl"):
        old.unlink()
    from . import colors as C
    src_cols = (params.mesh or {}).get("source_colors") or {}
    pal = C.palette(src_cols)
    furn_m = fixed.get("furniture")
    part_col = furn_m.metadata.get("hp3d_part_color") if furn_m is not None and isinstance(furn_m.metadata, dict) else None
    if part_col is not None:
        pal = dict(pal)
        pal["furniture"] = tuple(int(x) for x in part_col)
    for k, m in fixed.items():
        m.export(job / f"part_{k}.stl")          # STL: geometry only (no colour in the format)
    C.export_glb(fixed, job / "model.glb", pal)
    from .build3d import merged_for_stl
    merged = merged_for_stl(fixed)   # STL: one colour, door frames fused back into the walls
    merged.export(job / "model.stl")
    files = ["model.stl", "model.glb"] + [f"part_{k}.stl" for k in fixed]
    try:
        C.export_3mf(fixed, job / "model.3mf", pal, name=job.name); files.append("model.3mf")
    except Exception as e:  # noqa: BLE001
        log(f"3MF export unavailable: {e}")
    render(fixed, job / "preview.png", pal)
    files.append("preview.png")
    try:
        from .preview import render_underside
        render_underside(job / "model.stl", job / "preview_underside.png")
        files.append("preview_underside.png")
    except Exception as e:  # noqa: BLE001
        log(f"underside preview failed: {e}")
    try:
        render_overlay(job, polys)
        files.append("plan_overlay.png")
    except Exception as e:  # noqa: BLE001
        log(f"overlay failed: {e}")
    ext = (merged.bounds[1] - merged.bounds[0]).round(1).tolist()
    rp = job / "report.json"
    rep = json.loads(rp.read_text()) if rp.exists() else {"stages": {}}
    rep["stages"]["editor_rebuild"] = {"status": "ok", "seconds": round(time.time() - t, 2),
                                      "parts": list(fixed), "extent_in": ext,
                                      "extent_ft": [round(v / 12, 1) for v in ext],
                                      "watertight": {k: bool(m.is_watertight) for k, m in fixed.items()},
                                      "files": files, "logo": logo,
                                      "colors": {k: C.hex_of(C.color_of(k, pal)) for k in C.ordered(fixed)},
                                      "colors_from_file": sorted(k for k in src_cols if k in fixed
                                                                and not (k == "furniture" and part_col is not None))}
    rep["scale"] = {"px_per_inch": params.px_per_inch,
                    "source": "user-supplied in app" if "px_per_inch" in params.user_set else
                    (params.scale_source if params.scale_source and params.px_per_inch else None) or
                    ("estimated" if params.px_per_inch else "UNKNOWN - 1 px = 1 inch placeholder")}
    rep["build_params_in"] = {k: getattr(params, k) for k in ("wall_h", "floor_t", "sill", "lintel")}
    if polys.get("furniture"):
        rep["build_params_in"]["furniture_h_default"] = params.furniture_h
    rep["source_kind"] = params.source_kind
    rep["crop"] = {"box_px": params.crop, "source": params.crop_source}
    rep["polygon_counts"] = counts(polys)
    rep["assumptions"] = assumptions(params)
    if rep.get("confidence"):
        from .confidence import refresh_defaults
        rep["confidence"] = refresh_defaults(
            rep["confidence"], params.user_set, params.measured,
            {k: getattr(params, k) for k in ("sill", "lintel", "wall_h")})
    if logo["status"] != "ok":
        rep["assumptions"].append(f"Underside logo not added: {logo.get('reason')}")
    rep["in_app_edits"] = params.edits
    from . import quiz
    rep["review"] = quiz.review(job, params, polys)
    rp.write_text(json.dumps(rep, indent=1, default=str))
    params.save(job)
    log(f"rebuilt in {time.time() - t:.1f}s: extent {ext} in ({[round(v / 12, 1) for v in ext]} ft)")
    return rep


from .colors import PLAN_RGB as OVERLAY_COLORS  # noqa: E402  (same table as the app legend and the 3D colours)


def render_overlay(job: Path, polys: dict | None = None, selected: tuple | None = None, out: Path | None = None) -> Path:
    """2D top view: cropped plan faded, detected/edited polygons on top."""
    from PIL import Image, ImageDraw
    polys = load_polys(job) if polys is None else polys
    base = Image.open(job / "source_crop.png").convert("RGB")
    img = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.6)
    d = ImageDraw.Draw(img, "RGBA")
    draw_kinds = list(KINDS) + [k for k in polys if k not in KINDS and k in OVERLAY_COLORS]
    for k in draw_kinds:
        for i, p in enumerate(polys.get(k, [])):
            pts = [tuple(v) for v in p["outer"]]
            if len(pts) < 3:
                continue
            sel = selected == (k, i)
            c = furniture.plan_hex(p, "#{:02x}{:02x}{:02x}".format(*OVERLAY_COLORS[k]))
            rgb = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
            d.polygon(pts, fill=(255, 0, 200, 150) if sel else (*rgb, 140), outline=(255, 0, 200, 255) if sel else (*rgb, 255))
    out = out or (job / "plan_overlay.png")
    img.save(out)
    return out


def apply_actions(job: Path, params: JobParams, actions: list[dict], selected: tuple | None) -> tuple[list[str], bool, tuple | None]:
    """Apply parsed actions to params/polygons. Returns (messages, needs_rebuild, new_selection).
    Moves in inches need a known scale; without one they are refused, not guessed."""
    msgs, rebuild_needed = [], False
    polys = load_polys(job)
    changed_polys = False
    for a in actions:
        op = a["op"]
        if op == "help":
            msgs.append(HELP)
        elif op == "rebuild":
            rebuild_needed = True
        elif op == "undo":
            msgs.append("undo: use the Undo button (it restores the previous polygons and settings).")
        elif op == "redo":
            msgs.append("redo: use the Redo button (it puts back what you just undid).")
        elif op == "set":
            p, v = a["param"], a["value"]
            trial = {k: getattr(params, k) for k in ("wall_h", "floor_t", "sill", "lintel")}
            trial[p] = v
            if p == "furniture_h" and not v > 0:
                raise ValueError("furniture height must be more than 0")
            if p not in ("px_per_inch", "furniture_h") and not (trial["wall_h"] > 0 and trial["floor_t"] > 0 and 0 <= trial["sill"] < trial["lintel"] <= trial["wall_h"]):
                raise ValueError(f"{p}={v} in would break 0 <= sill < lintel <= wall height (now {trial}); nothing changed")
            params.set(p, v)
            if p == "px_per_inch":
                from .mesh_import import apply_mesh_heights
                apply_mesh_heights(params)     # heights measured in a 3D file follow the new size
            msgs.append(f"{p} set to {v}" + (" px/in" if p == "px_per_inch" else " in"))
            rebuild_needed = True
        else:
            if a["op"] in furniture.OPS:
                if a["op"] != "add_furniture" and (selected is None or selected[0] != "furniture"):
                    raise ValueError(f"'{a['op']}' needs a selected furniture piece - pick one in the Parts list first")
                polys, selected, msg = furniture.apply_action(polys, selected, a, params.px_per_inch)
                params.log(msg)
                msgs.append(msg)
                changed_polys = True
                rebuild_needed = True
                continue
            if selected is None and op != "add":
                raise ValueError(f"'{op}' needs a selected part - pick one in the Parts list first")
            if op == "move":
                f = 1.0 if a["unit"] == "px" else (params.px_per_inch or 0)
                if not f:
                    raise ValueError("can't move by inches/feet with an unknown scale; set px-per-inch or move in px")
                polys = move_part(polys, *selected, a["dx"] * f, a["dy"] * f)
                params.log(f"moved {selected[0]} #{selected[1]} by ({a['dx']}, {a['dy']}) {a['unit']}")
                msgs.append(f"moved {selected[0]} #{selected[1]} {a['dx']:+g},{a['dy']:+g} {a['unit']}")
            elif op == "delete":
                polys = delete_part(polys, *selected)
                params.log(f"deleted {selected[0]} #{selected[1]}")
                msgs.append(f"deleted {selected[0]} #{selected[1]}")
                selected = None
            elif op == "add":
                polys = add_rect(polys, a["kind"], *a["rect"])
                selected = (a["kind"], len(polys[a["kind"]]) - 1)
                params.log(f"added {a['kind']} rect {a['rect']} px")
                msgs.append(f"added {a['kind']} #{selected[1]} at {a['rect']} px")
            elif op == "resize":
                k, i = selected
                fx, fy = a["fx"], a["fy"]
                if a["axis"] == "long":  # widen/narrow along the part's long side
                    x0, y0, x1, y1 = next(p["bbox"] for p in list_parts(polys) if p["kind"] == k and p["index"] == i)
                    f = a["fx"]
                    fx, fy = (f, 1.0) if (x1 - x0) >= (y1 - y0) else (1.0, f)
                polys = resize_part(polys, k, i, fx, fy)
                params.log(f"resized {k} #{i} by x{fx:g}, y{fy:g}")
                msgs.append(f"resized {k} #{i} (x{fx:g}, y{fy:g})")
            changed_polys = True
            rebuild_needed = True
    if changed_polys:
        save_polys(job, polys)
    params.save(job)
    return msgs, rebuild_needed, selected
