"""Headless isometric PNG preview of an exported mesh (matplotlib, no GPU/Blender)."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402


def render(mesh_path: Path, png: Path) -> None:
    m = trimesh.load(mesh_path, force="mesh")
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(projection="3d")
    ax.add_collection3d(Poly3DCollection(m.triangles, facecolor="#cfcfcf", edgecolor="#555", linewidths=0.05))
    lo, hi = m.bounds
    span = (hi - lo).max()
    ax.set_xlim(lo[0], lo[0] + span); ax.set_ylim(lo[1], lo[1] + span); ax.set_zlim(0, span)
    ax.view_init(elev=45, azim=-60); ax.set_axis_off()
    fig.savefig(png, dpi=110, bbox_inches="tight")


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]))
