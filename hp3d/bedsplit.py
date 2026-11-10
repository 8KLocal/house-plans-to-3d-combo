"""Split a model into pieces that fit a printer bed, with dowels and numbers.

The bed is millimetres (default 256 x 256). Model inches become millimetres
through ``mm_per_unit`` (the same scale as the underside logo). A model that
already fits is not cut. Pieces are numbered left to right, then front to back.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh

PEG_MM = 4.0          # how far a dowel sticks into the neighbour
PEG_DIAMETER_MM = 3.0
LABEL_MM = 0.8        # raised digit height; it is not a measured building part

# 7-segment bits: a top, b upper right, c lower right, d bottom, e lower left, f upper left, g middle.
_DIGITS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgecd", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}


def fits_bed(mesh: trimesh.Trimesh, bed_mm: tuple[float, float], mm_per_unit: float) -> bool:
    lo, hi = mesh.bounds
    return (hi[0] - lo[0]) * mm_per_unit <= bed_mm[0] + 1e-6 and (hi[1] - lo[1]) * mm_per_unit <= bed_mm[1] + 1e-6


def _clip(mesh: trimesh.Trimesh, x0: float, x1: float, y0: float, y1: float) -> trimesh.Trimesh | None:
    planes = (
        (np.array([1.0, 0.0, 0.0]), np.array([x0, 0.0, 0.0])),
        (np.array([-1.0, 0.0, 0.0]), np.array([x1, 0.0, 0.0])),
        (np.array([0.0, 1.0, 0.0]), np.array([0.0, y0, 0.0])),
        (np.array([0.0, -1.0, 0.0]), np.array([0.0, y1, 0.0])),
    )
    current = mesh
    for normal, origin in planes:
        if current is None or len(getattr(current, "faces", [])) == 0:
            return None
        try:
            current = trimesh.intersections.slice_mesh_plane(current, normal, origin, cap=True)
        except Exception:  # noqa: BLE001
            try:
                current = trimesh.intersections.slice_mesh_plane(current, normal, origin, cap=False)
            except Exception:  # noqa: BLE001
                return None
    if current is None or len(current.faces) == 0:
        return None
    return current


def _digit(ch: str, height: float, depth: float) -> trimesh.Trimesh:
    """One digit in a local frame: x right, y up the digit, z out of the page. Origin at the lower left."""
    w, h = height * 0.6, height
    t = height * 0.14
    spans = {
        "a": ([0, h - t, 0], [w, h, depth]),
        "b": ([w - t, h / 2, 0], [w, h, depth]),
        "c": ([w - t, 0, 0], [w, h / 2, depth]),
        "d": ([0, 0, 0], [w, t, depth]),
        "e": ([0, 0, 0], [t, h / 2, depth]),
        "f": ([0, h / 2, 0], [t, h, depth]),
        "g": ([0, h / 2 - t / 2, 0], [w, h / 2 + t / 2, depth]),
    }
    boxes = [trimesh.creation.box(bounds=spans[s]) for s in _DIGITS[ch]]
    return trimesh.util.concatenate(boxes)


def number_mesh(number: int, height: float, depth: float) -> trimesh.Trimesh:
    text = str(int(number))
    gap = height * 0.2
    digits = []
    x = 0.0
    for ch in text:
        d = _digit(ch, height, depth)
        d.apply_translation([x, 0, 0])
        digits.append(d)
        x += height * 0.6 + gap
    return trimesh.util.concatenate(digits)


def _label_on_top(mesh: trimesh.Trimesh, number: int, mm_per_unit: float) -> trimesh.Trimesh:
    height = 8.0 / mm_per_unit
    depth = LABEL_MM / mm_per_unit
    mark = number_mesh(number, height, depth)
    lo, hi = mesh.bounds
    span = hi - lo
    # Keep the digits on the piece. Shrink if the piece is narrower than the mark.
    need = float(mark.bounds[1][0] - mark.bounds[0][0])
    if need > span[0] * 0.8 and need > 0:
        mark.apply_scale([span[0] * 0.8 / need, span[0] * 0.8 / need, 1.0])
    mlo, mhi = mark.bounds
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    mark.apply_translation([cx - (mlo[0] + mhi[0]) / 2, cy - (mlo[1] + mhi[1]) / 2, hi[2] - mlo[2]])
    return trimesh.util.concatenate([mesh, mark])


def _dowel(mesh: trimesh.Trimesh, axis: str, sign: int, mm_per_unit: float, hole: bool) -> tuple[trimesh.Trimesh, bool]:
    """A peg (sign < 0, sticking out of the min face) or a socket (sign > 0, in the max face)."""
    lo, hi = mesh.bounds
    length = PEG_MM / mm_per_unit
    radius = (PEG_DIAMETER_MM / 2) / mm_per_unit
    if axis == "x":
        y = (lo[1] + hi[1]) / 2
        z = (lo[2] + hi[2]) / 2
        x = hi[0] if sign > 0 else lo[0]
        center = [x + sign * length / 2, y, z]
        peg = trimesh.creation.cylinder(radius=radius * (1.15 if hole else 1.0), height=length, sections=12)
        peg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        peg.apply_translation(center)
    else:
        x = (lo[0] + hi[0]) / 2
        z = (lo[2] + hi[2]) / 2
        y = hi[1] if sign > 0 else lo[1]
        center = [x, y + sign * length / 2, z]
        peg = trimesh.creation.cylinder(radius=radius * (1.15 if hole else 1.0), height=length, sections=12)
        peg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        peg.apply_translation(center)
    if not hole:
        return trimesh.util.concatenate([mesh, peg]), True
    try:
        cut = trimesh.boolean.difference([mesh, peg], engine="manifold")
    except Exception:  # noqa: BLE001
        return mesh, False
    if cut is None or len(cut.faces) == 0 or cut.volume >= mesh.volume - 1e-9:
        return mesh, False
    return cut, True


def split_parts(parts: dict[str, trimesh.Trimesh], bed_mm: tuple[float, float],
                mm_per_unit: float) -> dict:
    """Cut every part on one grid. Empty cells are dropped. Numbers start at 1."""
    if mm_per_unit <= 0:
        raise ValueError("print scale must be more than 0 mm per model unit")
    bed = (float(bed_mm[0]), float(bed_mm[1]))
    if bed[0] <= 0 or bed[1] <= 0:
        raise ValueError("printer bed size must be more than 0 mm")
    body = trimesh.util.concatenate(list(parts.values()))
    lo, hi = body.bounds
    span = hi - lo
    bed_u = (bed[0] / mm_per_unit, bed[1] / mm_per_unit)
    report = {"bed_mm": list(bed), "mm_per_unit": mm_per_unit, "split": False, "pieces": [], "assumptions": []}
    if span[0] <= bed_u[0] + 1e-6 and span[1] <= bed_u[1] + 1e-6:
        report["assumptions"].append(f"Model fits a {bed[0]:g} x {bed[1]:g} mm bed, so it was not split.")
        return report
    peg_u = PEG_MM / mm_per_unit
    cell = (bed_u[0] - peg_u, bed_u[1] - peg_u)
    if cell[0] <= 1e-6 or cell[1] <= 1e-6:
        raise ValueError(f"bed {bed[0]:g} x {bed[1]:g} mm is smaller than a {PEG_MM:g} mm alignment dowel")
    nx = int(np.ceil((span[0] / cell[0]) - 1e-9))
    ny = int(np.ceil((span[1] / cell[1]) - 1e-9))
    pieces = []
    number = 0
    for iy in range(ny):
        for ix in range(nx):
            x0 = lo[0] + ix * cell[0]
            y0 = lo[1] + iy * cell[1]
            x1 = min(x0 + cell[0], hi[0])
            y1 = min(y0 + cell[1], hi[1])
            chunk = {}
            for name, mesh in parts.items():
                cut = _clip(mesh, x0, x1, y0, y1)
                if cut is not None and len(cut.faces):
                    chunk[name] = cut
            if not chunk:
                continue
            number += 1
            host = "floor" if "floor" in chunk else max(chunk, key=lambda k: chunk[k].area)
            notes = []
            # Dowels are applied after we know which cells are occupied; filled in below.
            chunk["_host"] = host
            chunk["_cell"] = (ix, iy)
            pieces.append({"number": number, "col": ix, "row": iy, "parts": chunk, "notes": notes})
    occupied = {(p["col"], p["row"]) for p in pieces}
    for piece in pieces:
        ix, iy = piece["col"], piece["row"]
        host = piece["parts"].pop("_host")
        piece["parts"].pop("_cell")
        chunk = piece["parts"]
        notes = piece["notes"]
        for axis, sign, neighbour in (
            ("x", -1, (ix - 1, iy) in occupied),
            ("x", 1, (ix + 1, iy) in occupied),
            ("y", -1, (ix, iy - 1) in occupied),
            ("y", 1, (ix, iy + 1) in occupied),
        ):
            if not neighbour:
                continue
            updated, ok = _dowel(chunk[host], axis, sign, mm_per_unit, hole=(sign > 0))
            chunk[host] = updated
            if not ok:
                notes.append(f"no {axis} socket; the matching dowel is still on the neighbouring piece")
        chunk[host] = _label_on_top(chunk[host], piece["number"], mm_per_unit)
        whole = trimesh.util.concatenate(list(chunk.values()))
        size_mm = (whole.bounds[1] - whole.bounds[0]) * mm_per_unit
        if size_mm[0] > bed[0] + 0.5 or size_mm[1] > bed[1] + 0.5:
            notes.append(f"piece is {size_mm[0]:.0f} x {size_mm[1]:.0f} mm, over the {bed[0]:g} x {bed[1]:g} mm bed")
        piece["size_mm"] = [round(float(v), 2) for v in size_mm]
    report["split"] = len(pieces) > 1
    report["pieces"] = pieces
    report["grid"] = [nx, ny]
    if report["split"]:
        report["assumptions"].append(
            f"Split into {len(pieces)} pieces for a {bed[0]:g} x {bed[1]:g} mm bed. "
            "Each piece is numbered on top. Dowels stick out of the low-X / low-Y faces; sockets are on the opposite faces. "
            + (" ".join(n for p in pieces for n in p["notes"]) if any(p["notes"] for p in pieces) else "Sockets were cut."))
    else:
        report["assumptions"].append(f"Model fits a {bed[0]:g} x {bed[1]:g} mm bed, so it was not split.")
    return report


def write_bed(parts: dict[str, trimesh.Trimesh], dest: Path, bed_mm, mm_per_unit: float) -> dict:
    """Write ``piece-N.stl`` and ``piece-N.3mf`` when the model does not fit. Always writes manifest.json."""
    info = split_parts(parts, (float(bed_mm[0]), float(bed_mm[1])), float(mm_per_unit))
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("piece-*"):
        old.unlink()
    files = []
    from . import colors as C
    for piece in info["pieces"] if info["split"] else []:
        n = piece["number"]
        stl = dest / f"piece-{n:02d}.stl"
        mf = dest / f"piece-{n:02d}.3mf"
        trimesh.util.concatenate(list(piece["parts"].values())).export(stl)
        try:
            C.export_3mf(piece["parts"], mf, name=f"piece-{n:02d}")
            piece_files = [stl.name, mf.name]
        except Exception:  # noqa: BLE001
            piece_files = [stl.name]
        files.extend(f"bed/{name}" for name in piece_files)
        piece["files"] = piece_files
        piece.pop("parts", None)
    manifest = {k: v for k, v in info.items() if k != "pieces"}
    manifest["pieces"] = [{k: v for k, v in p.items() if k != "parts"} for p in info["pieces"]]
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1))
    info["files"] = files
    info["manifest"] = "bed/manifest.json"
    return info
