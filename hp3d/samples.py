"""Small synthetic inputs for tests and demos (no customer data).

``sample_house_mesh`` is a one-room-plus-bedroom house as a single watertight
mesh, in inches (or any ``unit_scale``): 4 in slab, 6 in walls 96 in tall, a
front door opening with a header, an interior doorway, one window with a sill
and lintel, and one bed block. Everything the importer should find is known
exactly, so tests can check it.
"""
from __future__ import annotations

from pathlib import Path

import trimesh

TRUTH = {"width": 240.0, "depth": 180.0, "floor_t": 4.0, "wall_h": 96.0, "wall_t": 6.0,
         "door_w": 36.0, "door_h": 80.0, "sill": 36.0, "lintel": 84.0, "bed": (60.0, 80.0, 24.0)}


def _box(x0, y0, z0, x1, y1, z1):
    return trimesh.creation.box(bounds=[[x0, y0, z0], [x1, y1, z1]])


def sample_house_mesh(unit_scale: float = 1.0, with_bed: bool = True) -> trimesh.Trimesh:
    T = TRUTH
    W, D, F, H, t = T["width"], T["depth"], T["floor_t"], T["wall_h"], T["wall_t"]
    top = F + H
    solids = [_box(0, 0, 0, W, D, F),                        # slab
              _box(0, 0, F, W, t, top), _box(0, D - t, F, W, D, top),
              _box(0, 0, F, t, D, top), _box(W - t, 0, F, W, D, top),
              _box(150, 0, F, 150 + t, D, top)]              # interior wall at x=150..156
    house = trimesh.boolean.union(solids, engine="manifold")
    cuts = [_box(60, -1, F, 60 + T["door_w"], t + 1, F + T["door_h"]),          # front door, header above
            _box(149, 70, F, 157, 70 + T["door_w"], top + 1),                   # interior doorway, full height
            _box(80, D - t - 1, F + T["sill"], 128, D + 1, F + T["lintel"])]    # back window
    house = trimesh.boolean.difference([house, *cuts], engine="manifold")
    if with_bed:
        bw, bd, bh = T["bed"]
        house = trimesh.boolean.union([house, _box(170, 40, F, 170 + bw, 40 + bd, F + bh)], engine="manifold")
    house.apply_scale(unit_scale)
    return house


def write_sample_house(path: Path, unit_scale: float = 1.0, with_bed: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_house_mesh(unit_scale, with_bed).export(path)
    return path
