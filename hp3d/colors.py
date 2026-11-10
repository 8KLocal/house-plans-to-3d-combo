"""Part colours: one table for the 2D plan, the 3D preview and the 3MF/GLB exports.

Matches the plan legend (dark walls, blue windows, orange door frames, green
furniture) plus a light warm floor. STL cannot carry colour, so it stays plain
geometry; ``model.3mf`` gets one object per part with a 3MF base-material
colour, and ``model.glb`` gets one named mesh per part with a PBR base colour.

If the source 3D file already had colours (vertex/face/material), the importer
records the main colour per part (``params.mesh["source_colors"]``) and those
win over the defaults below; see ``mesh_import.source_colors``.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

# RGBA 0-255, keyed by built part name (build3d.build output).
PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "floor": (232, 218, 192, 255),        # light warm (oak/sand)
    "walls": (104, 107, 118, 255),        # dark neutral slate (plan: near-black)
    "windows": (60, 150, 220, 255),       # blue
    "door_frames": (230, 120, 50, 255),   # orange
    "furniture": (90, 160, 80, 255),      # green
    "stairs": (176, 122, 70, 255),        # stair treads
}
PART_LABELS = {"floor": "Floor", "walls": "Walls", "windows": "Windows", "door_frames": "Door frames",
               "furniture": "Furniture", "stairs": "Stairs"}
PART_ORDER = ("floor", "walls", "door_frames", "windows", "stairs", "furniture")
# Plan polygon kind -> built part name.
KIND_TO_PART = {"wall": "walls", "door": "door_frames", "window": "windows", "furniture": "furniture",
                "floor": "floor", "stair": "stairs"}
FALLBACK = (200, 200, 200, 255)

# 2D plan colours (the app and plan_overlay.png); same hues, walls drawn darker for contrast on paper.
PLAN_RGB = {"wall": (40, 40, 45), "door": (230, 120, 50), "window": (60, 150, 220), "furniture": (90, 160, 80),
            "stair": (176, 122, 70)}


def hex_of(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[int(c) for c in rgb[:3]])


PLAN_HEX = {k: hex_of(v) for k, v in PLAN_RGB.items()}


def palette(overrides: dict | None = None) -> dict[str, tuple[int, int, int, int]]:
    """Default part colours, with any per-part colours read from the source file on top."""
    pal = dict(PART_COLORS)
    for k, v in (overrides or {}).items():
        if v is not None and len(v) >= 3:
            c = [int(max(0, min(255, round(float(x))))) for x in v[:4]]
            pal[k] = tuple(c + [255] * (4 - len(c)))
    return pal


def color_of(part: str, pal: dict | None = None) -> tuple[int, int, int, int]:
    return (pal or PART_COLORS).get(part, FALLBACK)


def ordered(parts: dict) -> list[str]:
    return [k for k in PART_ORDER if k in parts] + [k for k in parts if k not in PART_ORDER]


# ---------------------------------------------------------------- exports

def _uses_face_colors(mesh) -> bool:
    return bool(isinstance(getattr(mesh, "metadata", None), dict) and mesh.metadata.get("hp3d_use_face_colors"))


def export_glb(parts: dict, path: Path, pal: dict | None = None) -> None:
    """One named mesh per part, each with a PBR base colour (shown by any glTF viewer)."""
    import trimesh
    from trimesh.visual.material import PBRMaterial
    scene = trimesh.Scene()
    for k in ordered(parts):
        m = parts[k].copy()
        if _uses_face_colors(m):
            scene.add_geometry(m, node_name=k, geom_name=k)
            continue
        c = color_of(k, pal)
        m.visual = trimesh.visual.TextureVisuals(material=PBRMaterial(
            name=k, baseColorFactor=[v / 255 for v in c], metallicFactor=0.0, roughnessFactor=0.9))
        scene.add_geometry(m, node_name=k, geom_name=k)
    scene.export(path)


def export_3mf(parts: dict, path: Path, pal: dict | None = None, name: str = "house") -> None:
    """3MF core spec: one mesh object per part with a base-material colour, grouped as one
    multi-part object so slicers (Bambu/Orca/Prusa/Cura) load it as one model with coloured parts.
    Units are millimetres, like trimesh's own 3MF export (1 model unit = 1 mm)."""
    keys = [k for k in ordered(parts) if len(parts[k].faces)]
    if not keys:
        raise ValueError("no parts to export")
    # Extra base materials so one Furniture object can carry a different colour per piece.
    extra: list[tuple[int, int, int, int]] = []
    extra_of: dict[tuple[int, int, int], int] = {}
    face_cols: dict[str, np.ndarray | None] = {}
    for k in keys:
        m = parts[k]
        if not _uses_face_colors(m):
            face_cols[k] = None
            continue
        fc = np.asarray(m.visual.face_colors)
        face_cols[k] = fc
        base = tuple(color_of(k, pal)[:3])
        for col in fc[:, :3]:
            key = (int(col[0]), int(col[1]), int(col[2]))
            if key == base or key in extra_of:
                continue
            extra_of[key] = len(keys) + len(extra)
            extra.append((*key, 255))
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
         f'<metadata name="Title">{name}</metadata>', '<metadata name="Application">hp3d (8K by AK)</metadata>',
         '<resources>', '<basematerials id="1">']
    for k in keys:
        x.append(f'<base name="{PART_LABELS.get(k, k)}" displaycolor="{hex_of(color_of(k, pal)).upper()}'
                 f'{color_of(k, pal)[3]:02X}"/>')
    for col in extra:
        x.append(f'<base name="Furniture" displaycolor="{hex_of(col).upper()}{col[3]:02X}"/>')
    x.append('</basematerials>')
    for i, k in enumerate(keys):
        m = parts[k]
        oid = i + 2
        x.append(f'<object id="{oid}" type="model" name="{PART_LABELS.get(k, k)}" pid="1" pindex="{i}"><mesh><vertices>')
        v = np.asarray(m.vertices, float)
        x.extend(f'<vertex x="{a:.6g}" y="{b:.6g}" z="{c:.6g}"/>' for a, b, c in v)
        x.append('</vertices><triangles>')
        fc = face_cols.get(k)
        faces = np.asarray(m.faces, int)
        if fc is not None and len(fc) == len(faces):
            base = tuple(int(c) for c in color_of(k, pal)[:3])
            lines = []
            for face, col in zip(faces, fc[:, :3]):
                key = (int(col[0]), int(col[1]), int(col[2]))
                pi = extra_of.get(key)
                a, b, c = (int(v) for v in face)
                if pi is None or key == base:
                    lines.append(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>')
                else:
                    lines.append(f'<triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{pi}"/>')
            x.extend(lines)
        else:
            x.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
        x.append('</triangles></mesh></object>')
    gid = len(keys) + 2
    x.append(f'<object id="{gid}" type="model" name="{name}"><components>')
    x.extend(f'<component objectid="{i + 2}"/>' for i in range(len(keys)))
    x.append('</components></object></resources>')
    x.append(f'<build><item objectid="{gid}"/></build></model>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", "\n".join(x))


def read_3mf_colors(path: Path) -> dict[str, str]:
    """Part label -> #RRGGBBAA as written in a 3MF (for tests and the report)."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    bases = [b.get("displaycolor") for b in root.iterfind(".//m:basematerials/m:base", ns)]
    out = {}
    for o in root.iterfind(".//m:object", ns):
        if o.get("pid") == "1" and o.get("pindex") is not None:
            out[o.get("name")] = bases[int(o.get("pindex"))]
    return out
