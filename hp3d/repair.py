"""Stage 4: mesh repair for printing with pyvista/pymeshfix (GPL-3.0).

Concatenated extrusions overlap and touch, which slicers dislike. MeshFix
turns each part into a single watertight manifold; we report before/after.
"""
from __future__ import annotations

import numpy as np
import trimesh


def stats(m: trimesh.Trimesh) -> dict:
    return {"vertices": int(len(m.vertices)), "faces": int(len(m.faces)),
            "watertight": bool(m.is_watertight), "winding_consistent": bool(m.is_winding_consistent),
            "bodies": int(len(m.split(only_watertight=False)))}


def repair(m: trimesh.Trimesh, joincomp: bool = True) -> trimesh.Trimesh:
    import pymeshfix

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
