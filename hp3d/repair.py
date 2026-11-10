"""Stage 4: mesh repair for printing with pyvista/pymeshfix (GPL-3.0).

Concatenated extrusions overlap and touch, which slicers dislike. MeshFix
turns each part into a single watertight manifold; we report before/after.
"""
from __future__ import annotations

import numpy as np
import pymeshfix
import trimesh


def stats(m: trimesh.Trimesh) -> dict:
    return {"vertices": int(len(m.vertices)), "faces": int(len(m.faces)),
            "watertight": bool(m.is_watertight), "winding_consistent": bool(m.is_winding_consistent),
            "bodies": int(len(m.split(only_watertight=False)))}


def _repair_bodies(m: trimesh.Trimesh, joincomp: bool) -> trimesh.Trimesh:
    # MeshFix keeps only the largest component unless joincomp=True. For a
    # house that would drop every detached wall, so we fix each body on its
    # own and recombine.
    fixed = []
    for body in m.split(only_watertight=False):
        v, f = pymeshfix.clean_from_arrays(np.asarray(body.vertices, float), np.asarray(body.faces, np.int32),
                                          joincomp=joincomp, remove_smallest_components=False)
        if len(f):
            fixed.append(trimesh.Trimesh(v, f, process=True))
    return trimesh.util.concatenate(fixed) if fixed else m


def repair(m: trimesh.Trimesh, joincomp: bool = True) -> trimesh.Trimesh:
    meta = dict(m.metadata) if isinstance(getattr(m, "metadata", None), dict) else {}
    if meta.get("hp3d_pieces"):
        colors = list(meta.get("hp3d_piece_colors") or [])
        fixed = []
        for i, piece in enumerate(meta["hp3d_pieces"]):
            body = _repair_bodies(piece, joincomp)
            if len(body.faces) == 0:
                continue
            if meta.get("hp3d_use_face_colors"):
                col = colors[i] if i < len(colors) and colors[i] is not None else (90, 160, 80, 255)
                body.visual.face_colors = col
            fixed.append(body)
        out = trimesh.util.concatenate(fixed) if fixed else m.copy()
    else:
        out = _repair_bodies(m, joincomp)
    if meta.get("hp3d_part_color") is not None:
        out.metadata["hp3d_part_color"] = tuple(meta["hp3d_part_color"])
    if meta.get("hp3d_use_face_colors"):
        out.metadata["hp3d_use_face_colors"] = True
    return out
