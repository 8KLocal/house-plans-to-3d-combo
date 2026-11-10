"""Stage 0: load a plan (PDF page, image, or a 3D file's top view) as an RGB numpy array.

``file_kind`` says what a file is from its name and first bytes, so the app can
refuse an unreadable type with a clear message instead of crashing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class Plan:
    path: Path
    rgb: np.ndarray            # H x W x 3 uint8
    is_pdf: bool
    page: int = 0
    dpi: float | None = None   # set for PDFs: px = pt * dpi / 72
    px_per_inch: float | None = None   # set for DXF drawings whose file states its units

    @property
    def pt_to_px(self) -> float | None:
        return None if self.dpi is None else self.dpi / 72.0


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
MESH_EXT = {".stl", ".obj", ".ply", ".off", ".3mf", ".glb", ".gltf"}
CAD_EXT = {".dxf"}
HELP_TYPES = ("a picture (PNG, JPG, TIFF, BMP, WEBP, GIF), a PDF, a DXF drawing, "
              "or a 3D model (STL, OBJ, PLY, 3MF, GLB/glTF)")
OPEN_PATTERNS = " ".join(f"*{e}" for e in sorted({".pdf"} | IMAGE_EXT | MESH_EXT | CAD_EXT))
DXF_MAX_PX = 1600
DXF_UNITS_IN = {"inches": 1.0, "inch": 1.0, "in": 1.0, "feet": 12.0, "foot": 12.0, "ft": 12.0, "millimeters": 1 / 25.4,
                "millimeter": 1 / 25.4, "mm": 1 / 25.4, "centimeters": 1 / 2.54, "cm": 1 / 2.54, "meters": 39.3701,
                "meter": 39.3701, "m": 39.3701}


class UnsupportedFile(ValueError):
    """A file this app can't read; the message is meant for the operator."""


def file_kind(path: str | Path) -> str:
    """'pdf' | 'image' | 'mesh'. Raises UnsupportedFile (plain message) for anything else."""
    path = Path(path)
    ext = path.suffix.lower()
    if not path.exists():
        raise UnsupportedFile(f"I can't find the file {path}.")
    if ext == ".pdf":
        return "pdf"
    if ext in MESH_EXT:
        return "mesh"
    if ext in CAD_EXT:
        return "cad"
    if ext in IMAGE_EXT:
        return "image"
    try:  # unknown extension: still accept it if it is really a picture
        with Image.open(path) as im:
            im.verify()
        return "image"
    except Exception:  # noqa: BLE001
        pass
    hint = {".dwg": "AutoCAD DWG drawings (save as DXF or PDF)", ".skp": "SketchUp models", ".rvt": "Revit models",
            ".heic": "iPhone photos", ".fbx": "FBX models", ".blend": "Blender files", ".doc": "Word files",
            ".docx": "Word files"}.get(ext, f"'{ext or 'no extension'}' files")
    raise UnsupportedFile(f"I can't read {hint} yet ({path.name}). Please save or export it as {HELP_TYPES}.")


def load_plan(path: str | Path, page: int = 0, dpi: float = 100.0) -> Plan:
    path = Path(path)
    kind = file_kind(path)
    if kind == "mesh":
        from . import mesh_import
        try:
            return Plan(path, mesh_import.plan_image(path), False)
        except mesh_import.UnreadableFile as e:
            raise UnsupportedFile(str(e)) from e
    if kind == "cad":
        return _load_dxf(path)
    if kind == "pdf":
        import pymupdf

        try:
            doc = pymupdf.open(path)
            pix = doc[page].get_pixmap(dpi=int(dpi), alpha=False)
        except Exception as e:  # noqa: BLE001
            raise UnsupportedFile(f"I couldn't open the PDF {path.name} ({type(e).__name__}: {e}).") from e
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
        return Plan(path, rgb.copy(), True, page, float(int(dpi)))
    try:
        rgb = np.array(Image.open(path).convert("RGB"))
    except Exception as e:  # noqa: BLE001
        raise UnsupportedFile(f"I couldn't open the picture {path.name} ({type(e).__name__}: {e}).") from e
    return Plan(path, rgb, False)


def _load_dxf(path: Path) -> Plan:
    """Draw a DXF black on white, keeping its stated units as the scale.

    Closed outlines are filled even-odd, so a wall drawn as an outer and an inner
    outline prints as a solid wall like on a paper plan; open lines are drawn
    thin. Layers, hatches, blocks and text are not interpreted.
    """
    import trimesh
    from PIL import ImageDraw
    try:
        d = trimesh.load(path)
        loops = [np.asarray(x)[:, :2] for x in d.discrete]
    except Exception as e:  # noqa: BLE001
        raise UnsupportedFile(f"I couldn't read the DXF drawing {path.name} ({type(e).__name__}: {e}).") from e
    if not loops:
        raise UnsupportedFile(f"{path.name} has no lines I can draw (text or blocks only?).")
    pts = np.vstack(loops)
    lo, hi = pts.min(0), pts.max(0)
    span = float(max(hi - lo))
    if span <= 0:
        raise UnsupportedFile(f"{path.name} has no size (all lines at one point).")
    r = DXF_MAX_PX / span
    pad = 20
    w, h = int((hi[0] - lo[0]) * r) + 2 * pad, int((hi[1] - lo[1]) * r) + 2 * pad
    img = Image.new("RGB", (w, h), "white")
    dr = ImageDraw.Draw(img)
    from shapely.geometry import Polygon as _P
    solid = _P()
    for x in loops:
        xy = [((px - lo[0]) * r + pad, (hi[1] - py) * r + pad) for px, py in x]
        if len(x) >= 4 and np.allclose(x[0], x[-1]):
            g = _P(xy).buffer(0)
            if g.area > 0:
                solid = solid.symmetric_difference(g)
                continue
        dr.line(xy, fill="black", width=2)
    for g in getattr(solid, "geoms", [solid]):
        if g.is_empty or not hasattr(g, "exterior"):
            continue
        dr.polygon(list(g.exterior.coords), fill="black")
        for hole in g.interiors:
            dr.polygon(list(hole.coords), fill="white")
    units = (getattr(d, "units", None) or "").lower()
    ppi = r / DXF_UNITS_IN[units] if units in DXF_UNITS_IN else None
    return Plan(path, np.array(img), False, px_per_inch=ppi)
